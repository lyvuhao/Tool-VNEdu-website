# =====================================================================
# AUTO ĐA NĂNG — NHẬP LIỆU TỰ ĐỘNG (CDP)
# =====================================================================
# Mô tả: Tool nhập liệu tự động vào form web VnEdu bằng Chrome CDP
# Dependencies: playwright
# =====================================================================

# =====================================================================
# PHẦN 1: IMPORT VÀ HẰNG SỐ
# =====================================================================

import tkinter as tk
from tkinter import ttk, messagebox
import copy
import time
import json
import os
import random
import re
import threading
import unicodedata
import queue
import ctypes
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import winsound
except ImportError:
    winsound = None

# ChromeBridge inline (gộp từ chrome_bridge.py để auto_danang.py tự chạy độc lập)
# =====================================================================
# CHROME BRIDGE — CDP Connection cho VnEdu Sổ Đầu Bài
# =====================================================================
# Mô tả: Kết nối Chrome đang mở qua Chrome DevTools Protocol (Playwright)
#         Đọc/điều khiển DOM VnEdu: dropdown, bảng, form nhập liệu
# Dependencies: playwright
# Sử dụng:
#   1. Mở Chrome: chrome.exe --remote-debugging-port=9224
#   2. Đăng nhập VnEdu → vào trang Sổ đầu bài
#   3. Gọi ChromeBridge từ worker thread
# =====================================================================

# =====================================================================
# PHẦN 1: IMPORT VÀ HẰNG SỐ
# =====================================================================

import json
import time
import logging
import urllib.request
import urllib.error
from typing import Optional, Tuple, List, Dict, Any

try:
    from playwright.sync_api import sync_playwright, Page, Browser
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

logger = logging.getLogger("chrome_bridge")

# --- Hằng số mặc định ---
DEFAULT_CDP_PORT = 9224
CDP_HOST = "127.0.0.1"
DEFAULT_TIMEOUT_MS = 10000       # 10s cho thao tác thông thường
NAV_TIMEOUT_MS = 30000           # 30s cho navigation (chọn dropdown → page reload)
FORM_WAIT_MS = 8000              # 8s chờ form popup mở
POST_SELECT_DELAY = 1.5          # Delay (s) sau khi chọn dropdown, chờ AJAX/reload
POST_CLICK_DELAY = 0.15          # Delay (s) dự phòng sau khi click nút ➕
POST_FILL_DELAY = 0.3            # Delay (s) sau khi fill mỗi field
POST_SAVE_DELAY = 1.5            # Delay (s) sau khi save, chờ dialog đóng

# URL pattern nhận diện trang VnEdu
VNEDU_URL_PATTERNS = ["vnedu.vn", "vnedu."]

# Thư mục user-data-dir riêng cho chế độ debug
# (Chrome yêu cầu user-data-dir riêng để bind debug port thành công)
import os as _os
CHROME_DEBUG_PROFILE_DIR = _os.path.join(
    _os.environ.get("USERPROFILE", "C:\\Users\\Default"),
    "chrome-debug-profile"
)

# Lệnh mở Chrome với CDP (bao gồm --user-data-dir riêng)
CHROME_LAUNCH_CMD = (
    'start chrome.exe --remote-debugging-port={port} '
    '--user-data-dir="{profile_dir}"'
)
VNEDU_SSO_LOGIN_URL = (
    "https://user.vnedu.vn/sso/?app_id=1&use_cache=1&continue="
    "http://diendan.vnedu.vn/security/ssoVnedu"
)
VNEDU_HOME_URL = "https://vnedu.vn/"


# =====================================================================
# PHẦN 2: HEALTH CHECK (không cần Playwright)
# =====================================================================

def cdp_health_check(port=DEFAULT_CDP_PORT):
    """Kiểm tra Chrome debugging port có accessible không.

    Gọi HTTP GET http://127.0.0.1:{port}/json/version
    Không cần Playwright — an toàn gọi từ bất kỳ thread nào.

    Args:
        port: CDP port number

    Returns:
        (success: bool, info: str)
    """
    try:
        url = f"http://{CDP_HOST}:{port}/json/version"
        req = urllib.request.Request(url, method='GET')
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            browser_info = data.get("Browser", "Unknown")
            ws_url = data.get("webSocketDebuggerUrl", "")
            return True, f"{browser_info}"
    except urllib.error.URLError:
        return False, (
            f"Chrome chưa mở debug port. Mở Chrome với:\n"
            f'  chrome.exe --remote-debugging-port={port} '
            f'--user-data-dir="{CHROME_DEBUG_PROFILE_DIR}"'
        )
    except Exception as e:
        return False, f"Lỗi: {type(e).__name__}: {str(e)[:80]}"


def list_cdp_tabs(port=DEFAULT_CDP_PORT):
    """Liệt kê tất cả tab đang mở trên Chrome debug port.

    Args:
        port: CDP port number

    Returns:
        list[dict] — [{title, url, id, type}, ...]
    """
    try:
        url = f"http://{CDP_HOST}:{port}/json"
        req = urllib.request.Request(url, method='GET')
        with urllib.request.urlopen(req, timeout=3) as resp:
            tabs = json.loads(resp.read().decode('utf-8'))
            return [
                {
                    "title": t.get("title", ""),
                    "url": t.get("url", ""),
                    "id": t.get("id", ""),
                    "type": t.get("type", ""),
                }
                for t in tabs
                if t.get("type") == "page"
            ]
    except Exception:
        return []


def is_cdp_target_closed_error(value):
    """True khi lỗi đến từ tab/browser CDP đã bị đóng hoặc context đã chết."""
    text = str(value or "").casefold()
    return (
        "targetclosederror" in text
        or "target page, context or browser has been closed" in text
        or "browser has been closed" in text
        or "target page has been closed" in text
    )


# =====================================================================
# PHẦN 3: CHROME BRIDGE CLASS
# =====================================================================

class ChromeBridge:
    """Kết nối Chrome qua CDP, đọc/điều khiển DOM VnEdu sổ đầu bài.

    QUAN TRỌNG: Tạo và sử dụng instance trong CÙNG MỘT THREAD.
    Playwright sync_api không thread-safe — tất cả method phải gọi
    từ thread đã gọi connect().

    Lifecycle:
        bridge = ChromeBridge(port=9224)
        ok, msg = bridge.connect()
        if ok:
            bridge.select_tuan("Tuần 25")
            bridge.select_lop("6A1")
            table = bridge.read_table()
            for row in table:
                bridge.type_one_entry(row, ppct=..., ...)
        bridge.disconnect()
    """

    def __init__(self, port=DEFAULT_CDP_PORT, timeout_ms=DEFAULT_TIMEOUT_MS):
        """Khởi tạo ChromeBridge.

        Args:
            port: CDP port (mặc định DEFAULT_CDP_PORT = 9224)
            timeout_ms: Timeout mặc định cho thao tác DOM (ms)
        """
        self.port = port
        self.timeout_ms = timeout_ms
        self._pw = None
        self.browser = None
        self.page = None
        self._connected = False

        # Cache selectors đã discover (tránh inspect lại mỗi lần)
        self._cached_selectors = {}

        # Stop signal cho batch operations
        self._stop_requested = False

        # Reference của dialog handler đang đăng ký (để gỡ idempotent)
        self._dialog_handler = None

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

    def ensure_chi_tiet_sodau_bai(self, username, password):
        """Đăng nhập SSO nếu cần và điều hướng tới màn Chi tiết sổ đầu bài."""
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        try:
            if self._is_v5_detail_ready():
                return True, {
                    "stage": "already_ready",
                    "url": self.page.url,
                    "title": self.page.title(),
                }

            current_url = (self.page.url or "").lower()
            has_teacher_portal_anchor = False
            try:
                has_teacher_portal_anchor = bool(self.page.evaluate(
                    """() => !!document.querySelector('a[href="/quan-ly-truong-hoc"]')"""
                ))
            except Exception:
                has_teacher_portal_anchor = False

            if "/v5/" not in current_url and not has_teacher_portal_anchor:
                self.page.goto(VNEDU_SSO_LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
                try:
                    self.page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass

                # Chờ SSO render xong: hoặc form login xuất hiện, hoặc đã có phiên.
                # Tránh race khi _is_login_page() bị kiểm tra quá sớm trước khi
                # form đăng nhập kịp hiển thị (redirect chain với use_cache).
                self._wait_until(
                    '''() => {
                        const user = document.querySelector('#txtUsername, input[placeholder="Tài khoản"]');
                        const pwd = document.querySelector('#txtPassword, input[placeholder="Mật khẩu"], input[type="password"]');
                        const href = String(location.href || '').toLowerCase();
                        const bodyText = String(document.body ? document.body.innerText || '' : '');
                        const hasTeacher = /Giáo viên/.test(bodyText);
                        const hasV5 = href.indexOf('/v5/') >= 0;
                        const hasPortal = !!document.querySelector('a[href="/quan-ly-truong-hoc"]');
                        return !!(user && pwd) || hasTeacher || hasV5 || hasPortal;
                    }''',
                    timeout_s=15,
                )

                if self._is_login_page():
                    if not str(username or "").strip() or not str(password or ""):
                        return False, (
                            "Chưa có phiên đăng nhập và chưa nhập đủ "
                            "Tài khoản/Mật khẩu VnEdu."
                        )
                    # Selector chính dùng id thật của form SSO (#txtUsername/#txtPassword),
                    # fallback theo placeholder/type cho chắc.
                    user_input = self.page.locator(
                        '#txtUsername, input[placeholder="Tài khoản"]'
                    ).first
                    pwd_input = self.page.locator(
                        '#txtPassword, input[placeholder="Mật khẩu"], input[type="password"]'
                    ).first
                    try:
                        user_input.wait_for(state="visible", timeout=8000)
                    except Exception:
                        pass
                    # .fill() tự focus + clear + set + bắn sự kiện input, ổn định hơn click+type.
                    user_input.fill(str(username or ""))
                    pwd_input.fill(str(password or ""))
                    try:
                        self.page.locator(
                            'button#btLogon, button:has-text("Đăng nhập")'
                        ).first.click(timeout=5000)
                    except Exception:
                        # Fallback: submit form bằng Enter nếu nút bị che/đổi layout.
                        try:
                            pwd_input.press("Enter")
                        except Exception:
                            pass

                    ok_wait, wait_err = self._wait_until(
                        '''() => {
                            const user = document.querySelector('input[placeholder="Tài khoản"]');
                            const pwd = document.querySelector('input[placeholder="Mật khẩu"], input[type="password"]');
                            const bodyText = String(document.body ? document.body.innerText || '' : '');
                            const hasTeacher = /Giáo viên/.test(bodyText);
                            const hasShortcut = !!document.querySelector('[id="Sổ đầu bài-shortcut"]');
                            const hasV5 = String(location.href || '').toLowerCase().indexOf('/v5/') >= 0;
                            return !(user && pwd) || hasTeacher || hasShortcut || hasV5;
                        }''',
                        timeout_s=30,
                    )
                    if not ok_wait and self._is_login_page():
                        login_error_text = self._get_login_error_text()
                        return False, (
                            "Đăng nhập không hoàn tất hoặc thông tin không đúng"
                            + (f": {login_error_text}" if login_error_text else "")
                            + (f" ({wait_err})" if wait_err else "")
                        )

                current_url = (self.page.url or "").lower()
                try:
                    has_teacher_portal_anchor = bool(self.page.evaluate(
                        """() => !!document.querySelector('a[href="/quan-ly-truong-hoc"]')"""
                    ))
                except Exception:
                    has_teacher_portal_anchor = False

                try:
                    if (
                        "vnedu.vn" not in current_url
                        and "diendan.vnedu.vn" not in current_url
                        and not has_teacher_portal_anchor
                    ):
                        self.page.goto(VNEDU_HOME_URL, wait_until="domcontentloaded", timeout=30000)
                        try:
                            self.page.wait_for_load_state("networkidle", timeout=10000)
                        except Exception:
                            pass
                except Exception:
                    pass

                ok_wait, wait_err = self._wait_until(
                    '''() => {
                        const href = String(location.href || '').toLowerCase();
                        const bodyText = String(document.body ? document.body.innerText || '' : '');
                        const hasTeacher = /Giáo viên/.test(bodyText);
                        const hasShortcut = !!document.querySelector('[id="Sổ đầu bài-shortcut"]');
                        const hasDetail = /Chi tiết sổ đầu bài/.test(bodyText);
                        const hasV5 = href.indexOf('/v5/') >= 0;
                        return hasV5 || hasTeacher || hasShortcut || hasDetail;
                    }''',
                    timeout_s=20,
                )
                if not ok_wait:
                    return False, f"Không vào được portal sau đăng nhập: {wait_err}"

                if not self._is_v5_detail_ready():
                    clicked_teacher = False
                    teacher_portal_href = self._get_teacher_portal_href()
                    if teacher_portal_href:
                        try:
                            self.page.goto(
                                teacher_portal_href,
                                wait_until="domcontentloaded",
                                timeout=30000,
                            )
                            clicked_teacher = True
                        except Exception:
                            clicked_teacher = False

                    try:
                        has_teacher_entry = bool(self.page.evaluate('''() => {
                            const bodyText = String(document.body ? document.body.innerText || '' : '');
                            return /Giáo viên/.test(bodyText);
                        }'''))
                    except Exception:
                        has_teacher_entry = False

                    if (not clicked_teacher) and has_teacher_entry:
                        for selector in [
                            'text="Giáo viên"',
                            'a:has-text("Giáo viên")',
                            'button:has-text("Giáo viên")',
                        ]:
                            try:
                                loc = self.page.locator(selector).first
                                if loc.count() > 0:
                                    loc.click(timeout=5000)
                                    clicked_teacher = True
                                    break
                            except Exception:
                                continue
                        if not clicked_teacher:
                            clicked_teacher = self._click_text_fallback("Giáo viên")
                        if not clicked_teacher:
                            return False, "Không tìm thấy nút Giáo viên sau đăng nhập"

                    if clicked_teacher:
                        ok_wait, wait_err = self._wait_until(
                            '''() => {
                                const href = String(location.href || '').toLowerCase();
                                const bodyText = String(document.body ? document.body.innerText || '' : '');
                                const hasShortcut = !!document.querySelector('[id="Sổ đầu bài-shortcut"]');
                                const hasDetail = /Chi tiết sổ đầu bài/.test(bodyText);
                                const hasV5 = href.indexOf('/v5/') >= 0;
                                return hasV5 || hasShortcut || hasDetail;
                            }''',
                            timeout_s=25,
                        )
                        if not ok_wait:
                            return False, f"Không chuyển được sang portal giáo viên/V5: {wait_err}"

                ok_wait, wait_err = self._wait_until(
                    '''() => {
                        const hasShortcut = !!document.querySelector('[id="Sổ đầu bài-shortcut"]');
                        const texts = Array.from(document.querySelectorAll('*'))
                            .map(el => String(el.innerText || '').trim());
                        if (hasShortcut) return true;
                        if (texts.includes('Chi tiết sổ đầu bài')) return true;
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
                    }''',
                    timeout_s=20,
                )
                if not ok_wait:
                    return False, f"Không vào được cổng Quản lý trường học/V5: {wait_err}"

                if not self._is_v5_detail_ready():
                    ok_wait, _ = self._wait_until(
                        '''() => {
                            const hasShortcut = !!document.querySelector('[id="Sổ đầu bài-shortcut"]');
                            const texts = Array.from(document.querySelectorAll('*'))
                                .map(el => String(el.innerText || '').trim());
                            return hasShortcut || texts.includes('Chi tiết sổ đầu bài');
                        }''',
                        timeout_s=12,
                    )
                    if not ok_wait:
                        return False, "Không thấy shortcut hoặc tree của Sổ đầu bài sau khi vào V5"

                    has_detail_tree = False
                    try:
                        has_detail_tree = bool(self.page.evaluate('''() => {
                            const texts = Array.from(document.querySelectorAll('*'))
                                .map(el => String(el.innerText || '').trim());
                            return texts.includes('Chi tiết sổ đầu bài');
                        }'''))
                    except Exception:
                        has_detail_tree = False

                    if not has_detail_tree:
                        clicked = False
                        for selector in ['[id="Sổ đầu bài-shortcut"]', 'text="Sổ đầu bài"']:
                            try:
                                loc = self.page.locator(selector).first
                                if loc.count() > 0:
                                    loc.click(timeout=5000)
                                    clicked = True
                                    break
                            except Exception:
                                continue
                        if not clicked:
                            clicked = self._click_text_fallback("Sổ đầu bài")
                        if not clicked:
                            return False, "Không tìm thấy shortcut Sổ đầu bài"

                        ok_wait, wait_err = self._wait_until(
                            '''() => {
                                const texts = Array.from(document.querySelectorAll('*'))
                                    .map(el => String(el.innerText || '').trim());
                                return texts.includes('Chi tiết sổ đầu bài');
                            }''',
                            timeout_s=12,
                        )
                        if not ok_wait:
                            return False, f"Không mở được module Sổ đầu bài: {wait_err}"

                    clicked = False
                    for selector in ['text="Chi tiết sổ đầu bài"', 'div.x-grid-cell-inner:has-text("Chi tiết sổ đầu bài")']:
                        try:
                            loc = self.page.locator(selector).first
                            if loc.count() > 0:
                                loc.click(timeout=5000)
                                clicked = True
                                break
                        except Exception:
                            continue
                    if not clicked:
                        clicked = self._click_text_fallback("Chi tiết sổ đầu bài")
                    if not clicked:
                        return False, "Không tìm thấy node Chi tiết sổ đầu bài"

                    ok_wait, wait_err = self._wait_until(
                        '''() => {
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
                        }''',
                        timeout_s=20,
                    )
                    if not ok_wait:
                        return False, f"Không vào được màn Chi tiết sổ đầu bài: {wait_err}"

            if not self._is_v5_detail_ready():
                current_url = (self.page.url or "").lower()
                if "/v5/" not in current_url:
                    teacher_portal_href = self._get_teacher_portal_href()
                    if not teacher_portal_href:
                        return False, (
                            "Không tìm thấy đường dẫn portal giáo viên từ trang hiện tại. "
                            f"URL hiện tại: {self.page.url}"
                        )
                    try:
                        self.page.goto(
                            teacher_portal_href,
                            wait_until="domcontentloaded",
                            timeout=30000,
                        )
                    except Exception as nav_err:
                        return False, f"Không vào được portal giáo viên từ trang hiện tại: {nav_err}"

                    ok_wait, wait_err = self._wait_until(
                        '''() => {
                            const href = String(location.href || '').toLowerCase();
                            const bodyText = String(document.body ? document.body.innerText || '' : '');
                            const hasShortcut = !!document.querySelector('[id="Sổ đầu bài-shortcut"]');
                            const hasDetail = /Chi tiết sổ đầu bài/.test(bodyText);
                            const hasV5 = href.indexOf('/v5/') >= 0;
                            return hasV5 || hasShortcut || hasDetail;
                        }''',
                        timeout_s=25,
                    )
                    if not ok_wait:
                        return False, f"Không chuyển được sang portal giáo viên/V5: {wait_err}"

                if not self._is_v5_detail_ready():
                    ok_wait, _ = self._wait_until(
                        '''() => {
                            const hasShortcut = !!document.querySelector('[id="Sổ đầu bài-shortcut"]');
                            const texts = Array.from(document.querySelectorAll('*'))
                                .map(el => String(el.innerText || '').trim());
                            return hasShortcut || texts.includes('Chi tiết sổ đầu bài');
                        }''',
                        timeout_s=12,
                    )
                    if not ok_wait:
                        return False, "Không thấy shortcut hoặc tree của Sổ đầu bài sau khi vào V5"

                    has_detail_tree = False
                    try:
                        has_detail_tree = bool(self.page.evaluate('''() => {
                            const texts = Array.from(document.querySelectorAll('*'))
                                .map(el => String(el.innerText || '').trim());
                            return texts.includes('Chi tiết sổ đầu bài');
                        }'''))
                    except Exception:
                        has_detail_tree = False

                    if not has_detail_tree:
                        clicked = False
                        for selector in ['[id="Sổ đầu bài-shortcut"]', 'text="Sổ đầu bài"']:
                            try:
                                loc = self.page.locator(selector).first
                                if loc.count() > 0:
                                    loc.click(timeout=5000)
                                    clicked = True
                                    break
                            except Exception:
                                continue
                        if not clicked:
                            clicked = self._click_text_fallback("Sổ đầu bài")
                        if not clicked:
                            return False, "Không tìm thấy shortcut Sổ đầu bài"

                        ok_wait, wait_err = self._wait_until(
                            '''() => {
                                const texts = Array.from(document.querySelectorAll('*'))
                                    .map(el => String(el.innerText || '').trim());
                                return texts.includes('Chi tiết sổ đầu bài');
                            }''',
                            timeout_s=12,
                        )
                        if not ok_wait:
                            return False, f"Không mở được module Sổ đầu bài: {wait_err}"

                    clicked = False
                    for selector in ['text="Chi tiết sổ đầu bài"', 'div.x-grid-cell-inner:has-text("Chi tiết sổ đầu bài")']:
                        try:
                            loc = self.page.locator(selector).first
                            if loc.count() > 0:
                                loc.click(timeout=5000)
                                clicked = True
                                break
                        except Exception:
                            continue
                    if not clicked:
                        clicked = self._click_text_fallback("Chi tiết sổ đầu bài")
                    if not clicked:
                        return False, "Không tìm thấy node Chi tiết sổ đầu bài"

                    ok_wait, wait_err = self._wait_until(
                        '''() => {
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
                        }''',
                        timeout_s=20,
                    )
                    if not ok_wait:
                        return False, f"Không vào được màn Chi tiết sổ đầu bài: {wait_err}"

            if not self._is_v5_detail_ready():
                return False, (
                    "Auto điều hướng chưa hoàn tất dù đã chạy hết flow. "
                    f"URL hiện tại: {self.page.url}"
                )

            return True, {
                "stage": "ready",
                "url": self.page.url,
                "title": self.page.title(),
            }
        except Exception as e:
            return False, f"Auto điều hướng lỗi: {type(e).__name__}: {str(e)[:140]}"

    # -----------------------------------------------------------------
    # 3.2: DROPDOWN OPERATIONS (Tuần, Lớp)
    # -----------------------------------------------------------------

    def get_dropdown_options(self, dropdown_label):
        """Đọc tất cả options từ dropdown trên trang VnEdu.

        VnEdu dùng ExtJS 4.x combobox (KHÔNG phải <select> HTML).
        Tìm combobox qua Ext.ComponentQuery → đọc store data.
        Fallback sang <select> nếu không có ExtJS.

        Args:
            dropdown_label: "tuan" | "lop" | "cap"

        Returns:
            (success, data) — data = dict{options, currentValue, ...} nếu success,
                              hoặc error string nếu fail
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        try:
            result = self.page.evaluate('''(label) => {
                // === Map label → ExtJS combobox name ===
                const nameMap = {
                    'tuan': 'cboTuanHoc',
                    'lop': 'cboLopHoc',
                    'cap': 'cboCapHoc'
                };
                const comboName = nameMap[label];
                if (!comboName) {
                    return {ok: false, error: 'Label không hợp lệ: ' + label};
                }

                // === Chiến lược 1: ExtJS combobox (VnEdu v5) ===
                if (typeof Ext !== 'undefined' && Ext.ComponentQuery) {
                    try {
                        const combos = Ext.ComponentQuery.query('combobox');
                        for (const c of combos) {
                            let name = '';
                            try { name = c.getName ? c.getName() : (c.name || ''); } catch(e) {}
                            if (name !== comboName) continue;

                            // Đọc store data
                            const store = c.store;
                            if (!store) {
                                return {ok: false, error: 'Combobox không có store: ' + comboName};
                            }

                            const options = [];
                            const items = (store.data && store.data.items) ? store.data.items : [];
                            const vf = c.valueField || 'id';
                            const df = c.displayField || 'name';
                            const currentVal = String(c.getValue ? c.getValue() : '');

                            for (const rec of items) {
                                const d = rec.data || rec;
                                options.push({
                                    value: String(d[vf] != null ? d[vf] : ''),
                                    text: String(d[df] != null ? d[df] : (d.name || d.ten || '')),
                                    selected: String(d[vf]) === currentVal
                                });
                            }

                            return {
                                ok: true,
                                type: 'extjs',
                                comboId: c.id || '',
                                comboName: name,
                                selectId: c.id || '',
                                selectName: name,
                                options: options,
                                currentValue: currentVal,
                                currentText: c.getRawValue ? c.getRawValue() : ''
                            };
                        }
                        return {ok: false, error: 'Không tìm thấy ExtJS combobox: ' + comboName};
                    } catch(e) {
                        // ExtJS lỗi → fallback sang <select>
                    }
                }

                // === Chiến lược 2: Fallback — tìm <select> HTML ===
                const keywords = {
                    'tuan': ['tuan', 'Tuan', 'week', 'ddlTuan', 'cboTuanHoc'],
                    'lop': ['lop', 'Lop', 'class', 'ddlLop', 'cboLopHoc'],
                    'cap': ['cap', 'Cap', 'level', 'ddlCap', 'cboCapHoc'],
                };
                const kws = keywords[label] || [label];

                let select = null;
                for (const sel of document.querySelectorAll('select')) {
                    const id = (sel.id || '').toLowerCase();
                    const name = (sel.name || '').toLowerCase();
                    for (const kw of kws) {
                        if (id.includes(kw.toLowerCase()) || name.includes(kw.toLowerCase())) {
                            select = sel;
                            break;
                        }
                    }
                    if (select) break;
                }

                if (!select) {
                    return {ok: false, error: 'Không tìm thấy dropdown (cả ExtJS lẫn select): ' + label};
                }

                const options = [];
                for (const opt of select.options) {
                    options.push({
                        value: opt.value,
                        text: opt.text.trim(),
                        selected: opt.selected
                    });
                }
                return {
                    ok: true,
                    type: 'select',
                    selectId: select.id,
                    selectName: select.name,
                    options: options,
                    currentValue: select.value,
                    currentText: select.options[select.selectedIndex]?.text?.trim() || ''
                };
            }''', dropdown_label)

            if result.get("ok"):
                # Cache combobox/selector info cho lần sau
                combo_id = result.get("comboId") or result.get("selectId", "")
                combo_name = result.get("comboName") or result.get("selectName", "")
                if combo_id:
                    self._cached_selectors[f"select_{dropdown_label}"] = combo_id
                if combo_name:
                    self._cached_selectors[f"combo_name_{dropdown_label}"] = combo_name
                logger.info(
                    f"Dropdown '{dropdown_label}': type={result.get('type','?')}, "
                    f"{len(result.get('options', []))} options, "
                    f"current='{result.get('currentText', '')}'"
                )
                return True, result
            else:
                return False, result.get("error", "Unknown error")

        except PlaywrightTimeout:
            return False, f"Timeout đọc dropdown '{dropdown_label}'"
        except Exception as e:
            return False, f"Lỗi đọc dropdown: {type(e).__name__}: {str(e)[:80]}"

    def select_dropdown(self, dropdown_label, target_text):
        """Chọn giá trị trong dropdown VnEdu.

        VnEdu dùng ExtJS 4.x combobox → setValue() + fireEvent('select').
        Fallback sang <select> nếu không có ExtJS.

        Args:
            dropdown_label: "tuan" | "lop"
            target_text: Text hiển thị của option (VD: "Tuần 25", "6A4")

        Returns:
            (success: bool, message: str)
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        try:
            # Bước 1: Tìm và chọn dropdown bằng JS
            result = self.page.evaluate('''(args) => {
                const [label, targetText] = args;

                // === Map label → ExtJS combobox name ===
                const nameMap = {
                    'tuan': 'cboTuanHoc',
                    'lop': 'cboLopHoc',
                    'cap': 'cboCapHoc'
                };
                const comboName = nameMap[label];

                // === Chiến lược 1: ExtJS combobox ===
                if (typeof Ext !== 'undefined' && Ext.ComponentQuery && comboName) {
                    try {
                        const combos = Ext.ComponentQuery.query('combobox');
                        for (const c of combos) {
                            let name = '';
                            try { name = c.getName ? c.getName() : (c.name || ''); } catch(e) {}
                            if (name !== comboName) continue;

                            const store = c.store;
                            if (!store) {
                                return {ok: false, error: 'Combobox không có store: ' + comboName};
                            }

                            const items = (store.data && store.data.items) ? store.data.items : [];
                            const vf = c.valueField || 'id';
                            const df = c.displayField || 'name';

                            // Tìm record khớp text — ưu tiên EXACT trước, sau đó mới
                            // fuzzy có ràng buộc biên để tránh "6A1" khớp nhầm "6A10"
                            // hoặc "Tuần 1" khớp nhầm "Tuần 10".
                            let matchRec = null;
                            const target = targetText.trim().toLowerCase();

                            // Pass 1: exact match
                            for (const rec of items) {
                                const d = rec.data || rec;
                                const text = String(d[df] != null ? d[df] : '').trim().toLowerCase();
                                if (text === target) {
                                    matchRec = rec;
                                    break;
                                }
                            }

                            // Pass 2: prefix match có ràng buộc biên (ký tự ngay sau
                            // target phải là hết chuỗi hoặc không phải chữ/số)
                            if (!matchRec && target) {
                                for (const rec of items) {
                                    const d = rec.data || rec;
                                    const text = String(d[df] != null ? d[df] : '').trim().toLowerCase();
                                    if (!text.startsWith(target)) continue;
                                    const nextChar = text.charAt(target.length);
                                    if (nextChar === '' || !/[a-z0-9]/.test(nextChar)) {
                                        matchRec = rec;
                                        break;
                                    }
                                }
                            }

                            // Thử tìm linh hoạt hơn (chỉ so sánh số)
                            if (!matchRec) {
                                const targetNum = targetText.replace(/\\D/g, '');
                                if (targetNum) {
                                    for (const rec of items) {
                                        const d = rec.data || rec;
                                        const text = String(d[df] != null ? d[df] : '');
                                        const optNum = text.replace(/\\D/g, '');
                                        if (optNum === targetNum) {
                                            matchRec = rec;
                                            break;
                                        }
                                    }
                                }
                            }

                            // Thử tìm theo value trực tiếp (nếu truyền số Tuần)
                            if (!matchRec) {
                                const targetNum = targetText.replace(/\\D/g, '');
                                if (targetNum) {
                                    for (const rec of items) {
                                        const d = rec.data || rec;
                                        if (String(d[vf]) === targetNum) {
                                            matchRec = rec;
                                            break;
                                        }
                                    }
                                }
                            }

                            if (!matchRec) {
                                return {
                                    ok: false,
                                    error: 'Không tìm thấy option: ' + targetText,
                                    available: items.slice(0, 10).map(r => {
                                        const d = r.data || r;
                                        return String(d[df] != null ? d[df] : '');
                                    })
                                };
                            }

                            // Chọn giá trị qua ExtJS API
                            const newValue = (matchRec.data || matchRec)[vf];
                            c.setValue(newValue);
                            c.fireEvent('select', c, [matchRec]);

                            return {
                                ok: true,
                                type: 'extjs',
                                selectedValue: String(newValue),
                                selectedText: c.getRawValue ? c.getRawValue() : String(targetText)
                            };
                        }
                        return {ok: false, error: 'Không tìm thấy ExtJS combobox: ' + comboName};
                    } catch(e) {
                        // ExtJS lỗi → fallback
                    }
                }

                // === Chiến lược 2: Fallback — <select> HTML ===
                const keywords = {
                    'tuan': ['tuan', 'Tuan', 'ddlTuan', 'cboTuanHoc'],
                    'lop': ['lop', 'Lop', 'ddlLop', 'cboLopHoc'],
                };
                const kws = keywords[label] || [label];

                let select = null;
                for (const sel of document.querySelectorAll('select')) {
                    const id = (sel.id || '').toLowerCase();
                    const name = (sel.name || '').toLowerCase();
                    for (const kw of kws) {
                        if (id.includes(kw.toLowerCase()) || name.includes(kw.toLowerCase())) {
                            select = sel;
                            break;
                        }
                    }
                    if (select) break;
                }
                if (!select) {
                    return {ok: false, error: 'Không tìm thấy dropdown (cả ExtJS lẫn select): ' + label};
                }

                // Tìm option khớp text — exact trước, prefix có ràng buộc biên sau
                let matchIdx = -1;
                const target = targetText.trim().toLowerCase();

                // Pass 1: exact match
                for (let i = 0; i < select.options.length; i++) {
                    const optText = select.options[i].text.trim().toLowerCase();
                    if (optText === target) {
                        matchIdx = i;
                        break;
                    }
                }

                // Pass 2: prefix match có ràng buộc biên (tránh "6A1" khớp "6A10")
                if (matchIdx < 0 && target) {
                    for (let i = 0; i < select.options.length; i++) {
                        const optText = select.options[i].text.trim().toLowerCase();
                        if (!optText.startsWith(target)) continue;
                        const nextChar = optText.charAt(target.length);
                        if (nextChar === '' || !/[a-z0-9]/.test(nextChar)) {
                            matchIdx = i;
                            break;
                        }
                    }
                }
                if (matchIdx < 0) {
                    const targetNum = targetText.replace(/\\D/g, '');
                    if (targetNum) {
                        for (let i = 0; i < select.options.length; i++) {
                            const optNum = select.options[i].text.replace(/\\D/g, '');
                            if (optNum === targetNum) {
                                matchIdx = i;
                                break;
                            }
                        }
                    }
                }
                if (matchIdx < 0) {
                    return {
                        ok: false,
                        error: 'Không tìm thấy option: ' + targetText,
                        available: Array.from(select.options).map(o => o.text.trim()).slice(0, 10)
                    };
                }

                select.selectedIndex = matchIdx;
                select.value = select.options[matchIdx].value;
                select.dispatchEvent(new Event('change', {bubbles: true}));
                select.dispatchEvent(new Event('input', {bubbles: true}));

                return {
                    ok: true,
                    type: 'select',
                    selectedValue: select.value,
                    selectedText: select.options[matchIdx].text.trim()
                };
            }''', [dropdown_label, target_text])

            if not result.get("ok"):
                err = result.get("error", "Unknown")
                avail = result.get("available", [])
                if avail:
                    err += f"\nCó sẵn: {', '.join(avail)}"
                return False, err

            # Bước 2: Chờ page cập nhật (AJAX hoặc full reload) theo điều kiện thật
            wait_ok, wait_state = self._wait_page_update(
                timeout_s=12,
                dropdown_label=dropdown_label,
                target_text=result.get("selectedText", target_text),
            )
            if not wait_ok:
                logger.debug(
                    f"Dropdown wait timeout for {dropdown_label}={target_text}: {wait_state}"
                )

            selected = result.get("selectedText", target_text)
            logger.info(f"Selected {dropdown_label}: {selected} (via {result.get('type', '?')})")
            return True, f"Đã chọn: {selected}"

        except PlaywrightTimeout:
            return False, f"Timeout chọn dropdown '{dropdown_label}'"
        except Exception as e:
            return False, f"Lỗi chọn dropdown: {type(e).__name__}: {str(e)[:80]}"

    def select_tuan(self, tuan_text):
        """Chọn Tuần từ dropdown.

        Args:
            tuan_text: "Tuần 25" hoặc "25" (tự thêm prefix)

        Returns:
            (success: bool, message: str)
        """
        # Normalize: nếu chỉ là số, thêm "Tuần "
        text = str(tuan_text).strip()
        if text.isdigit():
            text = f"Tuần {text}"
        return self.select_dropdown("tuan", text)

    def select_lop(self, lop_text):
        """Chọn Lớp từ dropdown.

        Args:
            lop_text: "6A1", "9A4", etc.

        Returns:
            (success: bool, message: str)
        """
        return self.select_dropdown("lop", str(lop_text).strip())

    def get_tuan_options(self):
        """Lấy danh sách Tuần có sẵn.

        Returns:
            (success, list[str]) — danh sách text options
        """
        ok, data = self.get_dropdown_options("tuan")
        if ok:
            return True, [opt["text"] for opt in data.get("options", [])]
        return False, data

    def get_lop_options_with_meta(self):
        """Lấy danh sách lớp kèm metadata ổn định từ combobox/store/DOM.

        Returns:
            (success, list[dict]) — mỗi phần tử có ít nhất `text`, có thể kèm
            `value`, `khoi`, `cap`, `source`.
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        try:
            result = self.page.evaluate('''() => {
                const out = [];
                const byKey = new Map();

                const cleanText = (text) => String(text == null ? '' : text)
                    .replace(/\\u00a0/g, ' ')
                    .replace(/\\s+/g, ' ')
                    .trim();
                const looksLikeClassName = (text) => {
                    const value = cleanText(text);
                    if (!value || value.startsWith('--')) return false;
                    if (/^tu[aà]n\\s*\\d+/i.test(value)) return false;
                    if (/^kh[oố]i\\s*\\d+/i.test(value)) return false;
                    if (value.length > 12 || /\\s/.test(value)) return false;
                    return /^\\d{1,2}[A-Za-zÀ-ỹ][A-Za-z0-9À-ỹ._/-]*$/.test(value);
                };
                const inferKhoi = (text) => {
                    const match = cleanText(text).match(/^(\\d{1,2})/);
                    return match ? match[1] : '';
                };
                const add = (payload, source) => {
                    const valueText = cleanText(payload && payload.text);
                    if (!looksLikeClassName(valueText)) return;
                    const key = valueText.toLowerCase();
                    const normalized = {
                        text: valueText,
                        value: cleanText(payload && payload.value),
                        khoi: cleanText(payload && payload.khoi) || inferKhoi(valueText),
                        cap: cleanText(payload && payload.cap),
                        source: source || cleanText(payload && payload.source),
                    };
                    if (byKey.has(key)) {
                        const current = byKey.get(key);
                        for (const field of ['value', 'khoi', 'cap', 'source']) {
                            if (!current[field] && normalized[field]) current[field] = normalized[field];
                        }
                        return;
                    }
                    byKey.set(key, normalized);
                    out.push(normalized);
                };

                const getCombo = () => {
                    if (!(window.Ext && Ext.ComponentQuery)) return null;
                    const combos = Ext.ComponentQuery.query('combobox');
                    const looksLikeClassStore = (combo) => {
                        try {
                            const rawValue = cleanText(combo.getRawValue ? combo.getRawValue() : combo.rawValue || '');
                            if (looksLikeClassName(rawValue)) return true;
                        } catch (e) {}
                        try {
                            const store = combo.getStore ? combo.getStore() : combo.store;
                            const items = store && store.getRange ? store.getRange() : (store && store.data && store.data.items) || [];
                            const displayField = combo.displayField || 'ten';
                            let sampleHits = 0;
                            for (const rec of Array.from(items).slice(0, 12)) {
                                let candidate = '';
                                try { candidate = rec.get ? rec.get(displayField) : ''; } catch (e) {}
                                if (!candidate && rec && rec.data) candidate = rec.data[displayField] || rec.data.ten || rec.data.lop || rec.data.ma_lop;
                                if (looksLikeClassName(candidate)) sampleHits++;
                            }
                            return sampleHits > 0;
                        } catch (e) {}
                        return false;
                    };
                    for (const combo of combos) {
                        try {
                            const name = combo.getName ? combo.getName() : (combo.name || '');
                            if (name === 'cboLopHoc' && looksLikeClassStore(combo)) return combo;
                        } catch (e) {}
                    }
                    return null;
                };

                const readRecord = (rec, fields) => {
                    for (const field of fields) {
                        try {
                            if (rec && rec.get && rec.get(field) != null) return rec.get(field);
                        } catch (e) {}
                        try {
                            if (rec && rec.data && rec.data[field] != null) return rec.data[field];
                        } catch (e2) {}
                        try {
                            if (rec && rec[field] != null) return rec[field];
                        } catch (e3) {}
                    }
                    return '';
                };

                const combo = getCombo();
                if (combo) {
                    try {
                        const currentText = combo.getRawValue ? combo.getRawValue() : combo.rawValue;
                        add({text: currentText}, 'current_raw');
                    } catch (e) {}

                    let picker = null;
                    try {
                        if (combo.expand) combo.expand();
                    } catch (e) {}
                    try {
                        picker = combo.getPicker ? combo.getPicker() : combo.picker;
                    } catch (e) {}

                    const store = (() => {
                        try { return combo.getStore ? combo.getStore() : combo.store; } catch (e) {}
                        return combo.store || null;
                    })();
                    const displayField = combo.displayField || 'ten';
                    const valueField = combo.valueField || 'id';
                    const fieldCandidates = [displayField, 'ten', 'name', 'text', 'label', 'lop', 'ma_lop'];
                    const valueCandidates = [valueField, 'id', 'value', 'ma_lop', 'lop_hoc_id'];
                    const khoiCandidates = ['khoi', 'khoi_hoc', 'grade', 'khoiHoc'];
                    const capCandidates = ['cap', 'cap_hoc', 'capHoc', 'caphoc'];

                    const readStoreItems = (items, source) => {
                        if (!items) return;
                        for (const rec of Array.from(items)) {
                            add({
                                text: readRecord(rec, fieldCandidates),
                                value: readRecord(rec, valueCandidates),
                                khoi: readRecord(rec, khoiCandidates),
                                cap: readRecord(rec, capCandidates),
                            }, source);
                        }
                    };

                    if (store) {
                        try {
                            if (store.getRange) readStoreItems(store.getRange(), 'store.getRange');
                        } catch (e) {}
                        try {
                            if (store.data && store.data.items) readStoreItems(store.data.items, 'store.data');
                        } catch (e) {}
                        try {
                            if (store.snapshot && store.snapshot.items) readStoreItems(store.snapshot.items, 'store.snapshot');
                        } catch (e) {}
                        try {
                            if (store.allData && store.allData.items) readStoreItems(store.allData.items, 'store.allData');
                        } catch (e) {}
                    }

                    try {
                        const root = picker && picker.el && picker.el.dom ? picker.el.dom : null;
                        if (root) {
                            for (const node of root.querySelectorAll('.x-boundlist-item, .x-combo-list-item, option')) {
                                add({text: node.textContent || node.innerText || ''}, 'picker_dom');
                            }
                        }
                    } catch (e) {}

                    try {
                        for (const node of document.querySelectorAll('.x-boundlist-item, .x-combo-list-item')) {
                            add({text: node.textContent || node.innerText || ''}, 'boundlist_dom');
                        }
                    } catch (e) {}

                    try {
                        if (combo.collapse) combo.collapse();
                    } catch (e) {}
                }

                for (const sel of document.querySelectorAll('select')) {
                    const key = String(sel.id || sel.name || '').toLowerCase();
                    if (!key.includes('lop') && !key.includes('class') && !key.includes('cbolophoc')) continue;
                    for (const opt of Array.from(sel.options || [])) {
                        add({
                            text: opt.textContent || opt.innerText || opt.text || '',
                            value: opt.value || '',
                        }, 'select');
                    }
                }

                return {ok: out.length > 0, options: out};
            }''')
            if not result.get("ok"):
                return False, "Không tìm thấy danh sách lớp từ combobox/store/DOM"
            return True, list(result.get("options", []) or [])
        except PlaywrightTimeout:
            return False, "Timeout đọc danh sách lớp"
        except Exception as e:
            return False, f"Lỗi đọc danh sách lớp: {type(e).__name__}: {str(e)[:120]}"

    def get_lop_options(self):
        """Lấy danh sách text lớp có sẵn."""
        ok, records_or_error = self.get_lop_options_with_meta()
        if not ok:
            return False, records_or_error
        options = [
            str(item.get("text", "")).strip()
            for item in list(records_or_error or [])
            if str(item.get("text", "")).strip()
        ]
        return True, options

    def fetch_lop_options_for_weeks_service(self, tuan_nums, timeout_s=10.0, concurrency=6):
        """Đọc lớp thật của nhiều tuần qua service, không thay đổi dropdown trên web."""
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        ordered_weeks = []
        seen_weeks = set()
        for item in list(tuan_nums or []):
            try:
                week_num = int(item)
            except Exception:
                continue
            if week_num < 1 or week_num > 52 or week_num in seen_weeks:
                continue
            seen_weeks.add(week_num)
            ordered_weeks.append(week_num)
        if not ordered_weeks:
            return False, "Danh sách tuần cần quét đang trống"

        try:
            result = self.page.evaluate(
                '''async (args) => {
                    const cleanText = (value) => String(value == null ? '' : value)
                        .replace(/\\u00a0/g, ' ')
                        .replace(/\\s+/g, ' ')
                        .trim();
                    const looksLikeClassName = (value) => {
                        const text = cleanText(value);
                        if (!text || text.length > 16 || /\\s/.test(text)) return false;
                        return /^\\d{1,2}[A-Za-zÀ-ỹ][A-Za-z0-9À-ỹ._/-]*$/.test(text);
                    };
                    const getCombo = (name) => {
                        if (!(window.Ext && Ext.ComponentQuery)) return null;
                        for (const combo of Ext.ComponentQuery.query('combobox')) {
                            try {
                                const comboName = combo.getName ? combo.getName() : (combo.name || '');
                                if (comboName === name) return combo;
                            } catch (e) {}
                        }
                        return null;
                    };
                    const firstValue = (record, fields) => {
                        for (const field of fields) {
                            if (record && record[field] != null && cleanText(record[field])) {
                                return cleanText(record[field]);
                            }
                        }
                        return '';
                    };
                    const findRecordArray = (payload) => {
                        const candidates = [];
                        const visit = (value, depth) => {
                            if (depth > 5 || value == null) return;
                            if (Array.isArray(value)) {
                                candidates.push(value);
                                for (const item of value.slice(0, 4)) {
                                    if (item && typeof item === 'object') visit(item, depth + 1);
                                }
                                return;
                            }
                            if (typeof value !== 'object') return;
                            for (const child of Object.values(value)) visit(child, depth + 1);
                        };
                        visit(payload, 0);
                        let best = [];
                        let bestScore = -1;
                        for (const items of candidates) {
                            let score = 0;
                            for (const record of items.slice(0, 30)) {
                                if (!record || typeof record !== 'object') continue;
                                const label = firstValue(record, [
                                    'ten', 'name', 'text', 'label', 'lop', 'ten_lop',
                                    'lop_hoc', 'ma_lop', 'display'
                                ]);
                                if (looksLikeClassName(label)) score += 4;
                                if (firstValue(record, ['id', 'value', 'lop_id', 'lop_hoc_id', 'ma_lop'])) {
                                    score += 1;
                                }
                            }
                            if (score > bestScore) {
                                bestScore = score;
                                best = items;
                            }
                        }
                        return bestScore > 0 ? best : [];
                    };

                    const capCombo = getCombo('cboCapHoc');
                    const capHoc = capCombo
                        ? (capCombo.getValue ? capCombo.getValue() : capCombo.value)
                        : 2;
                    const token = typeof myToken !== 'undefined'
                        ? (typeof myToken === 'function' ? myToken() : myToken)
                        : '';
                    const userId = typeof myUserId !== 'undefined'
                        ? (typeof myUserId === 'function' ? myUserId() : myUserId)
                        : '';
                    const namHoc = String(
                        typeof phpviet_nam_hoc_v5 !== 'undefined' ? phpviet_nam_hoc_v5 : 2025
                    );

                    async function fetchWeek(weekNum) {
                        const params = new URLSearchParams();
                        params.set('my_token', String(token || ''));
                        params.set('my_user_id', String(userId || ''));
                        params.set('app_nam_hoc', namHoc);
                        params.set('ma_quyen', 'view_detail,export,ket_chuyen');
                        params.set('cap_hoc', String(capHoc || 2));
                        params.set('tuan_hoc', String(weekNum));
                        params.set('page', '1');
                        params.set('start', '0');
                        params.set('limit', '500');
                        const url = './?call=app.sodaubai.serv.so_dau_bai.getDanhSachLopByCap&'
                            + params.toString();
                        const controller = new AbortController();
                        const timeoutId = setTimeout(
                            () => controller.abort(),
                            Math.max(parseInt(args.timeoutMs || 0, 10) || 0, 3000)
                        );
                        try {
                            const response = await fetch(url, {
                                method: 'GET',
                                credentials: 'same-origin',
                                signal: controller.signal,
                            });
                            const rawText = await response.text();
                            if (!response.ok) {
                                return {
                                    week: weekNum,
                                    ok: false,
                                    error: 'HTTP ' + response.status,
                                };
                            }
                            let payload = null;
                            try {
                                payload = JSON.parse(rawText);
                            } catch (e) {
                                return {
                                    week: weekNum,
                                    ok: false,
                                    error: 'Service lớp không trả JSON hợp lệ',
                                };
                            }
                            const rawRecords = findRecordArray(payload);
                            const records = [];
                            const seen = new Set();
                            for (const record of rawRecords) {
                                if (!record || typeof record !== 'object') continue;
                                const text = firstValue(record, [
                                    'ten', 'name', 'text', 'label', 'lop', 'ten_lop',
                                    'lop_hoc', 'ma_lop', 'display'
                                ]);
                                if (!looksLikeClassName(text)) continue;
                                const key = text.toLocaleLowerCase('vi');
                                if (seen.has(key)) continue;
                                seen.add(key);
                                const inferredKhoi = (text.match(/^(\\d{1,2})/) || [])[1] || '';
                                records.push({
                                    text,
                                    value: firstValue(record, [
                                        'id', 'value', 'lop_id', 'lop_hoc_id', 'ma_lop'
                                    ]),
                                    khoi: firstValue(record, [
                                        'khoi', 'khoi_hoc', 'ma_khoi', 'khoiHoc', 'grade'
                                    ]) || inferredKhoi,
                                    cap: firstValue(record, [
                                        'cap', 'cap_hoc', 'capHoc', 'caphoc'
                                    ]) || cleanText(capHoc),
                                    source: 'service.getDanhSachLopByCap',
                                });
                            }
                            return {week: weekNum, ok: true, records};
                        } catch (error) {
                            const isTimeout = String(error && error.name ? error.name : '') === 'AbortError';
                            return {
                                week: weekNum,
                                ok: false,
                                error: isTimeout
                                    ? 'Timeout đọc service lớp'
                                    : String(error && error.message ? error.message : error),
                            };
                        } finally {
                            clearTimeout(timeoutId);
                        }
                    }

                    const queue = Array.from(args.weeks || []);
                    const results = [];
                    const workerCount = Math.max(
                        1,
                        Math.min(parseInt(args.concurrency || 4, 10) || 4, queue.length)
                    );
                    async function worker() {
                        while (queue.length) {
                            const weekNum = queue.shift();
                            if (!weekNum) break;
                            results.push(await fetchWeek(weekNum));
                        }
                    }
                    await Promise.all(Array.from({length: workerCount}, () => worker()));
                    results.sort((a, b) => a.week - b.week);

                    const byClass = new Map();
                    const classesByWeek = {};
                    const weekErrors = [];
                    const weeksScanned = [];
                    for (const item of results) {
                        if (!item.ok) {
                            weekErrors.push({week: item.week, message: item.error || 'Lỗi không xác định'});
                            continue;
                        }
                        weeksScanned.push(item.week);
                        classesByWeek[String(item.week)] = [];
                        for (const record of item.records || []) {
                            const key = record.text.toLocaleLowerCase('vi');
                            classesByWeek[String(item.week)].push(record.text);
                            if (!byClass.has(key)) {
                                byClass.set(key, {...record, weeks: [item.week]});
                                continue;
                            }
                            const current = byClass.get(key);
                            if (!current.weeks.includes(item.week)) current.weeks.push(item.week);
                            for (const field of ['value', 'khoi', 'cap', 'source']) {
                                if (!current[field] && record[field]) current[field] = record[field];
                            }
                        }
                    }
                    const records = Array.from(byClass.values()).sort(
                        (a, b) => a.text.localeCompare(b.text, 'vi', {numeric: true})
                    );
                    return {
                        ok: records.length > 0,
                        options: records.map((item) => item.text),
                        records,
                        classes_by_week: classesByWeek,
                        weeks_scanned: weeksScanned,
                        week_errors: weekErrors,
                        source: 'service',
                        error: records.length ? '' : 'Service không trả về lớp hợp lệ',
                    };
                }''',
                {
                    "weeks": ordered_weeks,
                    "timeoutMs": max(int(timeout_s * 1000), 3000),
                    "concurrency": max(1, min(int(concurrency or 4), 8)),
                },
            )
            if result.get("ok"):
                return True, result
            return False, result.get("error", "Service không trả về danh sách lớp")
        except PlaywrightTimeout:
            return False, "Timeout quét danh sách lớp qua service"
        except Exception as e:
            return False, f"Lỗi quét lớp qua service: {type(e).__name__}: {str(e)[:140]}"

    def discover_lop_options_for_weeks(self, tuan_nums=None, restore_selection=True):
        """Quét danh sách lớp xuất hiện trong nhiều tuần để tránh thiếu lớp theo học kỳ."""
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        original_selection = {}
        try:
            ok_current, current = self.get_current_selection()
            if ok_current and isinstance(current, dict):
                original_selection = current
        except Exception:
            original_selection = {}

        try:
            if tuan_nums is None:
                ok_weeks, week_options = self.get_tuan_options()
                if not ok_weeks:
                    return False, f"Không đọc được danh sách tuần: {week_options}"
                parsed_weeks = []
                for item in week_options:
                    match = re.search(r"\d+", str(item or ""))
                    if match:
                        parsed_weeks.append(int(match.group()))
                tuan_nums = parsed_weeks

            ordered_weeks = []
            seen_weeks = set()
            for item in list(tuan_nums or []):
                try:
                    week_num = int(item)
                except Exception:
                    continue
                if week_num < 1 or week_num > 52 or week_num in seen_weeks:
                    continue
                seen_weeks.add(week_num)
                ordered_weeks.append(week_num)

            if not ordered_weeks:
                ok_lop, options = self.get_lop_options()
                return (True, {"options": options, "weeks_scanned": []}) if ok_lop else (False, options)

            ok_service, service_payload = self.fetch_lop_options_for_weeks_service(ordered_weeks)
            if ok_service:
                return True, service_payload
            logger.warning(f"Service quét lớp không khả dụng, fallback UI: {service_payload}")

            class_names = []
            seen_classes = set()
            class_records = {}
            week_errors = []
            scanned_weeks = []
            for week_num in ordered_weeks:
                if self.should_stop:
                    break
                ok_select, msg_select = self.select_tuan(f"Tuần {week_num}")
                if not ok_select:
                    week_errors.append({"week": week_num, "message": msg_select})
                    continue
                scanned_weeks.append(week_num)
                ok_lop, lop_options = self.get_lop_options_with_meta()
                if not ok_lop:
                    week_errors.append({"week": week_num, "message": str(lop_options)})
                    continue
                for item in list(lop_options or []):
                    normalized = str(item.get("text", "") or "").strip()
                    key = normalized.lower()
                    if not normalized or key in seen_classes:
                        if normalized and key in class_records:
                            existing = class_records[key]
                            for field in ("value", "khoi", "cap", "source"):
                                incoming = str(item.get(field, "") or "").strip()
                                if not existing.get(field) and incoming:
                                    existing[field] = incoming
                        continue
                    seen_classes.add(key)
                    class_names.append(normalized)
                    class_records[key] = {
                        "text": normalized,
                        "value": str(item.get("value", "") or "").strip(),
                        "khoi": str(item.get("khoi", "") or "").strip(),
                        "cap": str(item.get("cap", "") or "").strip(),
                        "source": str(item.get("source", "") or "").strip(),
                    }

            if restore_selection and original_selection:
                try:
                    if original_selection.get("tuan"):
                        self.select_tuan(original_selection.get("tuan"))
                    if original_selection.get("lop"):
                        self.select_lop(original_selection.get("lop"))
                except Exception:
                    pass

            return True, {
                "options": class_names,
                "records": [class_records[key] for key in sorted(class_records.keys())],
                "weeks_scanned": scanned_weeks,
                "week_errors": week_errors,
            }
        except Exception as e:
            return False, f"Lỗi quét lớp theo tuần: {type(e).__name__}: {str(e)[:140]}"

    def get_current_selection(self):
        """Đọc Tuần + Lớp đang chọn hiện tại.

        Returns:
            (success, dict) — {tuan: str, lop: str}
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"
        try:
            result = self.page.evaluate('''() => {
                const info = {tuan: '', lop: '', cap: ''};
                if (typeof Ext !== 'undefined' && Ext.ComponentQuery) {
                    try {
                        const combos = Ext.ComponentQuery.query('combobox');
                        for (const combo of combos) {
                            const name = combo.getName ? combo.getName() : (combo.name || '');
                            const rawText = combo.getRawValue ? combo.getRawValue() : (combo.rawValue || '');
                            if (name === 'cboTuanHoc' && rawText) info.tuan = String(rawText).trim();
                            else if (name === 'cboLopHoc' && rawText) info.lop = String(rawText).trim();
                            else if (name === 'cboCapHoc' && rawText) info.cap = String(rawText).trim();
                        }
                    } catch (e) {}
                }
                for (const sel of document.querySelectorAll('select')) {
                    const id = (sel.id || sel.name || '').toLowerCase();
                    const text = sel.options[sel.selectedIndex]?.text?.trim() || '';
                    if (!info.tuan && id.includes('tuan')) info.tuan = text;
                    else if (!info.lop && id.includes('lop')) info.lop = text;
                    else if (!info.cap && id.includes('cap')) info.cap = text;
                }
                return info;
            }''')
            return True, result
        except Exception as e:
            return False, str(e)

    def get_current_user_full_name(self):
        """Đọc họ tên người dùng hiện tại từ session VnEdu.

        Returns:
            (success, str) — tên đầy đủ giáo viên đang đăng nhập
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"
        try:
            result = self.page.evaluate(
                '''() => {
                    const candidates = [
                        typeof phpviet_user_full_name !== 'undefined' ? phpviet_user_full_name : '',
                        typeof phpviet_user_name !== 'undefined' ? phpviet_user_name : '',
                    ];
                    for (const value of candidates) {
                        const text = String(value || '').trim();
                        if (text) return text;
                    }
                    return '';
                }'''
            )
            user_full_name = str(result or "").strip()
            if user_full_name:
                return True, user_full_name
            return False, "Không đọc được tên người dùng hiện tại từ session"
        except Exception as e:
            return False, str(e)

    def _is_v5_stats_ready(self):
        """Kiểm tra đã vào đúng màn Thống kê nhập sổ đầu bài hay chưa."""
        if not self.page:
            return False
        try:
            if self._is_session_expired_page():
                return False
            return bool(self.page.evaluate('''() => {
                const visible = (el) => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 1 && rect.height > 1 &&
                        style.visibility !== 'hidden' && style.display !== 'none';
                };
                const weekInput = document.querySelector('input[name="cboTuanHoc"]');
                const classInputs = Array.from(document.querySelectorAll('input[name="cboLopHoc"]'))
                    .filter(visible);
                const bodyText = String(document.body ? document.body.innerText || '' : '');
                return !!weekInput &&
                    classInputs.length >= 2 &&
                    /GV chưa nhập lịch/i.test(bodyText) &&
                    /Thống kê nhập sổ đầu bài/i.test(bodyText);
            }'''))
        except Exception:
            return False

    def ensure_thong_ke_nhap_sodau_bai(self, username="", password=""):
        """Đảm bảo tab hiện tại đang ở màn Thống kê nhập sổ đầu bài."""
        if not self.is_connected:
            return False, "Chưa kết nối CDP"
        try:
            if self._is_session_expired_page():
                ok_ready, ready_payload = self.ensure_chi_tiet_sodau_bai(username, password)
                if not ok_ready:
                    return False, ready_payload
            if self._is_v5_stats_ready():
                return True, {
                    "stage": "already_ready",
                    "url": self.page.url,
                    "title": self.page.title(),
                }

            ok_ready, ready_payload = self.ensure_chi_tiet_sodau_bai(username, password)
            if not ok_ready:
                return False, ready_payload

            clicked = False
            for selector in [
                'text="Thống kê nhập sổ đầu bài"',
                'div.x-grid-cell-inner:has-text("Thống kê nhập sổ đầu bài")',
            ]:
                try:
                    loc = self.page.locator(selector).first
                    if loc.count() > 0:
                        loc.click(timeout=5000)
                        clicked = True
                        break
                except Exception:
                    continue
            if not clicked:
                clicked = self._click_text_fallback("Thống kê nhập sổ đầu bài")
            if not clicked:
                return False, "Không tìm thấy node 'Thống kê nhập sổ đầu bài'"

            ok_wait, wait_err = self._wait_until(
                '''() => {
                    const visible = (el) => {
                        if (!el) return false;
                        const rect = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        return rect.width > 1 && rect.height > 1 &&
                            style.visibility !== 'hidden' && style.display !== 'none';
                    };
                    const weekInput = document.querySelector('input[name="cboTuanHoc"]');
                    const classInputs = Array.from(document.querySelectorAll('input[name="cboLopHoc"]'))
                        .filter(visible);
                    const bodyText = String(document.body ? document.body.innerText || '' : '');
                    return !!weekInput &&
                        classInputs.length >= 2 &&
                        /GV chưa nhập lịch/i.test(bodyText);
                }''',
                timeout_s=20,
            )
            if not ok_wait:
                return False, f"Không vào được màn Thống kê nhập sổ đầu bài: {wait_err}"

            return True, {
                "stage": "stats_ready",
                "url": self.page.url,
                "title": self.page.title(),
            }
        except Exception as e:
            return False, f"Lỗi mở màn Thống kê nhập sổ đầu bài: {type(e).__name__}: {str(e)[:120]}"

    def _stats_select_field(self, field_kind, target_text):
        """Chọn filter trên màn thống kê theo kind: week | grade | class."""
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        field_kind = str(field_kind or "").strip().lower()
        target_text = str(target_text or "").strip()
        if field_kind not in {"week", "grade", "class"}:
            return False, f"field_kind không hợp lệ: {field_kind}"
        if not target_text:
            return False, "Thiếu target_text"

        try:
            result = self.page.evaluate(
                '''async (args) => {
                    const cleanText = (text) => String(text == null ? '' : text)
                        .replace(/\\u00a0/g, ' ')
                        .replace(/\\s+/g, ' ')
                        .trim();
                    const norm = (text) => cleanText(text).toLowerCase();
                    const visible = (el) => {
                        if (!el) return false;
                        const rect = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        return rect.width > 1 && rect.height > 1 &&
                            style.visibility !== 'hidden' && style.display !== 'none';
                    };
                    const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
                    const fireClick = (el) => {
                        if (!el) return false;
                        const evtOpts = {bubbles: true, cancelable: true, view: window};
                        el.dispatchEvent(new MouseEvent('mouseover', evtOpts));
                        el.dispatchEvent(new MouseEvent('mousedown', evtOpts));
                        el.dispatchEvent(new MouseEvent('mouseup', evtOpts));
                        el.click();
                        return true;
                    };
                    const getFieldInput = (kind) => {
                        if (kind === 'week') {
                            return Array.from(document.querySelectorAll('input[name="cboTuanHoc"]'))
                                .find(visible) || null;
                        }
                        const classInputs = Array.from(document.querySelectorAll('input[name="cboLopHoc"]'))
                            .filter(visible);
                        if (kind === 'grade') return classInputs[0] || null;
                        if (kind === 'class') return classInputs[classInputs.length - 1] || null;
                        return null;
                    };
                    const input = getFieldInput(args.kind);
                    if (!input) {
                        return {ok: false, error: 'Không tìm thấy input filter ' + args.kind};
                    }
                    const currentText = cleanText(input.value || input.getAttribute('value') || '');
                    if (norm(currentText) === norm(args.targetText)) {
                        return {ok: true, selectedText: currentText, unchanged: true};
                    }

                    const wrapper = input.closest('.x-form-trigger-wrap') || input.parentElement;
                    const trigger = wrapper
                        ? wrapper.querySelector('.x-form-trigger, .x-trigger-index-0')
                        : null;
                    if (!trigger) {
                        return {ok: false, error: 'Không tìm thấy trigger của field ' + args.kind};
                    }
                    fireClick(trigger);
                    await sleep(180);

                    const items = Array.from(document.querySelectorAll('.x-boundlist-item, .x-combo-list-item, li'))
                        .filter(visible);
                    const exact = items.find((node) => norm(node.textContent || node.innerText || '') === norm(args.targetText));
                    const partial = items.find((node) => norm(node.textContent || node.innerText || '').includes(norm(args.targetText)));
                    const targetNode = exact || partial;
                    if (!targetNode) {
                        return {
                            ok: false,
                            error: 'Không thấy option ' + args.targetText,
                            available: items.slice(0, 40).map(node => cleanText(node.textContent || node.innerText || '')).filter(Boolean),
                        };
                    }
                    fireClick(targetNode);
                    await sleep(280);

                    const selectedText = cleanText(input.value || input.getAttribute('value') || '');
                    return {
                        ok: norm(selectedText) === norm(args.targetText) || norm(selectedText).includes(norm(args.targetText)),
                        selectedText,
                    };
                }''',
                {"kind": field_kind, "targetText": target_text},
            )
            if result.get("ok"):
                return True, str(result.get("selectedText") or target_text).strip()
            available = list(result.get("available") or [])
            message = str(result.get("error", f"Không chọn được {target_text}")).strip()
            if available:
                message += f" | Có sẵn: {', '.join(available[:12])}"
            return False, message
        except PlaywrightTimeout:
            return False, f"Timeout chọn filter thống kê {field_kind}"
        except Exception as e:
            return False, f"Lỗi chọn filter thống kê {field_kind}: {type(e).__name__}: {str(e)[:120]}"

    def stats_select_week(self, tuan_text):
        """Chọn tuần trên màn thống kê."""
        text = str(tuan_text or "").strip()
        if text.isdigit():
            text = f"Tuần {text}"
        return self._stats_select_field("week", text)

    def stats_select_grade(self, grade_text):
        """Chọn khối trên màn thống kê."""
        return self._stats_select_field("grade", grade_text)

    def stats_select_class(self, lop_text):
        """Chọn lớp trên màn thống kê."""
        return self._stats_select_field("class", lop_text)

    def stats_get_filter_options(self, field_kind):
        """Đọc option thật đang được cấp quyền trên màn thống kê."""
        if not self.is_connected:
            return False, "Chưa kết nối CDP"
        if field_kind not in {"week", "grade", "class"}:
            return False, f"Filter thống kê không hợp lệ: {field_kind}"
        try:
            result = self.page.evaluate(
                '''async (kind) => {
                    const cleanText = (value) => String(value == null ? '' : value)
                        .replace(/\\u00a0/g, ' ')
                        .replace(/\\s+/g, ' ')
                        .trim();
                    const visible = (el) => {
                        if (!el) return false;
                        const rect = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        return rect.width > 1 && rect.height > 1
                            && style.visibility !== 'hidden' && style.display !== 'none';
                    };
                    const fireClick = (el) => {
                        if (!el) return false;
                        const opts = {bubbles: true, cancelable: true, view: window};
                        el.dispatchEvent(new MouseEvent('mousedown', opts));
                        el.dispatchEvent(new MouseEvent('mouseup', opts));
                        el.click();
                        return true;
                    };
                    const classInputs = Array.from(document.querySelectorAll('input[name="cboLopHoc"]'))
                        .filter(visible);
                    let input = null;
                    if (kind === 'week') {
                        input = Array.from(document.querySelectorAll('input[name="cboTuanHoc"]'))
                            .find(visible) || null;
                    } else if (kind === 'grade') {
                        input = classInputs[0] || null;
                    } else {
                        input = classInputs[classInputs.length - 1] || null;
                    }
                    if (!input) return {ok: false, error: 'Không tìm thấy input ' + kind};

                    let combo = null;
                    if (window.Ext && Ext.ComponentQuery) {
                        for (const item of Ext.ComponentQuery.query('combobox')) {
                            try {
                                const itemInput = item.inputEl && item.inputEl.dom ? item.inputEl.dom : null;
                                if (itemInput === input || (itemInput && itemInput.id === input.id)) {
                                    combo = item;
                                    break;
                                }
                            } catch (e) {}
                        }
                    }
                    if (combo) {
                        try {
                            if (combo.expand) combo.expand();
                        } catch (e) {}
                    } else {
                        const wrapper = input.closest('.x-form-trigger-wrap') || input.parentElement;
                        const trigger = wrapper
                            ? wrapper.querySelector('.x-form-trigger, .x-trigger-index-0')
                            : null;
                        if (!fireClick(trigger)) {
                            return {ok: false, error: 'Không mở được dropdown ' + kind};
                        }
                    }
                    let store = combo ? (combo.getStore ? combo.getStore() : combo.store) : null;
                    const displayField = combo ? (combo.displayField || 'ten') : 'ten';
                    let items = [];
                    for (let attempt = 0; attempt < 10; attempt++) {
                        store = combo ? (combo.getStore ? combo.getStore() : combo.store) : null;
                        items = store && store.getRange
                            ? store.getRange()
                            : (store && store.data && store.data.items) || [];
                        if (items.length) break;
                        await new Promise((resolve) => setTimeout(resolve, 120));
                    }
                    const options = [];
                    const seen = new Set();
                    for (const record of Array.from(items || [])) {
                        let value = '';
                        try {
                            value = record.get ? record.get(displayField) : '';
                        } catch (e) {}
                        if (!value && record && record.data) {
                            value = record.data[displayField]
                                || record.data.ten
                                || record.data.name
                                || record.data.text
                                || '';
                        }
                        const text = cleanText(value);
                        const key = text.toLocaleLowerCase('vi');
                        if (!text || seen.has(key)) continue;
                        seen.add(key);
                        options.push(text);
                    }
                    if (!options.length) {
                        const domItems = Array.from(
                            document.querySelectorAll('.x-boundlist-item, .x-combo-list-item')
                        ).filter(visible);
                        for (const node of domItems) {
                            const text = cleanText(node.textContent || node.innerText || '');
                            const key = text.toLocaleLowerCase('vi');
                            if (!text || seen.has(key)) continue;
                            seen.add(key);
                            options.push(text);
                        }
                    }
                    try {
                        if (combo && combo.collapse) combo.collapse();
                    } catch (e) {}
                    return options.length
                        ? {ok: true, options}
                        : {ok: false, error: 'Dropdown ' + kind + ' không có option'};
                }''',
                field_kind,
            )
            if result.get("ok"):
                return True, list(result.get("options") or [])
            return False, result.get("error", f"Không đọc được option {field_kind}")
        except Exception as e:
            return False, f"Lỗi đọc option thống kê {field_kind}: {type(e).__name__}: {str(e)[:120]}"

    def stats_set_missing_only(self, enabled=True):
        """Bật/tắt checkbox 'GV chưa nhập lịch' trên màn thống kê."""
        if not self.is_connected:
            return False, "Chưa kết nối CDP"
        target_enabled = bool(enabled)
        try:
            result = self.page.evaluate(
                '''async (targetEnabled) => {
                    const cleanText = (text) => String(text == null ? '' : text)
                        .replace(/\\u00a0/g, ' ')
                        .replace(/\\s+/g, ' ')
                        .trim();
                    const visible = (el) => {
                        if (!el) return false;
                        const rect = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        return rect.width > 1 && rect.height > 1 &&
                            style.visibility !== 'hidden' && style.display !== 'none';
                    };
                    const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
                    const fireClick = (el) => {
                        if (!el) return false;
                        const evtOpts = {bubbles: true, cancelable: true, view: window};
                        el.dispatchEvent(new MouseEvent('mouseover', evtOpts));
                        el.dispatchEvent(new MouseEvent('mousedown', evtOpts));
                        el.dispatchEvent(new MouseEvent('mouseup', evtOpts));
                        el.click();
                        return true;
                    };
                    const fieldCandidates = Array.from(document.querySelectorAll('.x-form-item, .x-field'))
                        .filter((node) => /GV chưa nhập lịch/i.test(cleanText(node.textContent || '')) && visible(node))
                        .sort((a, b) => cleanText(a.textContent || '').length - cleanText(b.textContent || '').length);
                    const field = fieldCandidates[0] || null;
                    if (!field) {
                        return {ok: false, error: 'Không tìm thấy checkbox GV chưa nhập lịch'};
                    }
                    const isChecked = field.classList.contains('x-form-cb-checked');
                    if (isChecked === targetEnabled) {
                        return {ok: true, enabled: isChecked, unchanged: true};
                    }
                    const target = field.querySelector('input, .x-form-cb, .x-form-checkbox') || field;
                    fireClick(target);
                    await sleep(250);
                    return {
                        ok: field.classList.contains('x-form-cb-checked') === targetEnabled,
                        enabled: field.classList.contains('x-form-cb-checked'),
                    };
                }''',
                target_enabled,
            )
            if result.get("ok"):
                return True, "Đã bật lọc GV chưa nhập" if result.get("enabled") else "Đã tắt lọc GV chưa nhập"
            return False, result.get("error", "Không đổi được checkbox GV chưa nhập lịch")
        except Exception as e:
            return False, f"Lỗi đổi checkbox GV chưa nhập lịch: {type(e).__name__}: {str(e)[:120]}"

    def stats_read_missing_teacher_rows(self, expected_class="", timeout_s=6.0):
        """Đọc bảng thống kê GV chưa nhập lịch ở màn thống kê nhập sổ đầu bài."""
        if not self.is_connected:
            return False, "Chưa kết nối CDP"
        try:
            if self._is_session_expired_page():
                return False, "Phiên làm việc VnEdu đã hết hiệu lực. Hãy đăng nhập lại rồi chạy thống kê."
            expected_class = str(expected_class or "").strip()
            if expected_class:
                expected_json = json.dumps(expected_class)
                predicate_js = (
                    "() => {"
                    f"const expectedClass = {expected_json};"
                    "const cleanText = (text) => String(text == null ? '' : text)"
                    ".replace(/\\u00a0/g, ' ')"
                    ".replace(/\\s+/g, ' ')"
                    ".trim()"
                    ".toLowerCase();"
                    "const target = cleanText(expectedClass);"
                    "if (!target) return true;"
                    "for (const table of Array.from(document.querySelectorAll('table'))) {"
                    "const text = cleanText(table.innerText || '');"
                    "if (!text.includes('họ tên') || !text.includes('tổng tiết')) continue;"
                    "const rows = Array.from(table.querySelectorAll('tr')).map((tr) => cleanText(tr.textContent || ''));"
                    "const classLabel = rows.find((row) => row.startsWith('lớp:')) || '';"
                    "if (classLabel === ('lớp: ' + target) || classLabel.endsWith(target)) return true;"
                    "}"
                    "return false;"
                    "}"
                )
                wait_ok, wait_err = self._wait_until(
                    predicate_js,
                    timeout_s=max(float(timeout_s or 0), 0.5),
                )
                if not wait_ok:
                    return False, f"Timeout chờ bảng thống kê cập nhật cho lớp {expected_class}: {wait_err}"
            result = self.page.evaluate('''() => {
                const cleanText = (text) => String(text == null ? '' : text)
                    .replace(/\\u00a0/g, ' ')
                    .replace(/\\s+/g, ' ')
                    .trim();
                const visible = (el) => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 1 && rect.height > 1 &&
                        style.visibility !== 'hidden' && style.display !== 'none';
                };
                const dayMap = [2, 3, 4, 5, 6, 7, 8];
                const headers = {
                    morning: dayMap.map((thu, idx) => ({thu, buoi: 'Sáng', columnIndex: 5 + idx})),
                    afternoon: dayMap.map((thu, idx) => ({thu, buoi: 'Chiều', columnIndex: 12 + idx})),
                };
                const table = Array.from(document.querySelectorAll('table'))
                    .find((node) => /Họ tên/.test(cleanText(node.innerText || '')) && /Tổng tiết/.test(cleanText(node.innerText || '')));
                if (!table) {
                    return {ok: false, error: 'Không tìm thấy bảng thống kê giáo viên'};
                }

                const classLabel = Array.from(table.querySelectorAll('tr'))
                    .map((tr) => cleanText(tr.textContent || ''))
                    .find((text) => /^Lớp:/i.test(text)) || '';
                const classText = cleanText(classLabel.replace(/^Lớp:\\s*/i, ''));

                const rows = [];
                for (const tr of Array.from(table.querySelectorAll('tr'))) {
                    const cells = Array.from(tr.querySelectorAll('td'));
                    if (cells.length < 19) continue;
                    const stt = cleanText(cells[0].textContent || '');
                    const teacher = cleanText(cells[1].textContent || '');
                    const subject = cleanText(cells[2].textContent || '');
                    const className = cleanText(cells[3].textContent || '');
                    const totalText = cleanText(cells[4].textContent || '');
                    if (!teacher || /^tổng$/i.test(teacher) || /^tổng$/i.test(stt)) continue;
                    const matchTotal = totalText.match(/\\d+/);
                    const totalMissing = matchTotal ? parseInt(matchTotal[0], 10) : 0;
                    const counts = [];
                    for (const item of [...headers.morning, ...headers.afternoon]) {
                        const text = cleanText((cells[item.columnIndex] || {}).textContent || '');
                        const match = text.match(/\\d+/);
                        if (!match) continue;
                        counts.push({
                            thu: item.thu,
                            buoi: item.buoi,
                            count: parseInt(match[0], 10),
                            column_index: item.columnIndex,
                        });
                    }
                    rows.push({
                        stt,
                        teacher_name: teacher,
                        mon_hoc: subject,
                        lop: className || classText,
                        total_missing: totalMissing,
                        counts,
                    });
                }

                return {
                    ok: true,
                    lop: classText,
                    rows,
                };
            }''')
            if result.get("ok"):
                return True, result
            return False, result.get("error", "Không đọc được bảng thống kê GV chưa nhập")
        except PlaywrightTimeout:
            return False, "Timeout đọc bảng thống kê GV chưa nhập"
        except Exception as e:
            return False, f"Lỗi đọc bảng thống kê GV chưa nhập: {type(e).__name__}: {str(e)[:120]}"

    # -----------------------------------------------------------------
    # 3.3: TABLE READING (Bảng sổ đầu bài)
    # -----------------------------------------------------------------

    def read_table(self):
        """Đọc bảng sổ đầu bài → danh sách structured rows.

        Parse HTML table với xử lý rowspan cho cột Thứ và Buổi.
        VnEdu dùng <td> cho cả header lẫn data (không có <th>).
        Nút ➕ là <a class="add add_tiet_so_dau_bai">.
        Mỗi row đại diện cho 1 tiết học (1 dòng trong bảng).

        Returns:
            (success, data) — data = list[dict] nếu success:
                [{
                    index: int,          — vị trí row trong table (0-based)
                    thu: str,            — "2", "3", ... "7" (thứ trong tuần)
                    thu_full: str,       — "2\n09/03/2026" (text gốc)
                    buoi: str,           — "Sáng" | "Chiều"
                    tiet: str,           — "1", "2", "3", "4", "5"
                    mon_hoc: str,        — tên môn học (nếu đã nhập)
                    ppct: str,           — số PPCT hiển thị trên bảng (nếu có)
                    has_data: bool,      — true nếu tiết đã có dữ liệu
                    has_add_btn: bool,   — true nếu có nút ➕
                    add_btn_index: int,  — vị trí trong danh sách nút ➕ toàn bảng
                }]
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        try:
            result = self.page.evaluate('''() => {
                // === Tìm bảng dữ liệu chính ===
                // Ưu tiên table.table (VnEdu v5), fallback tìm table nhiều rowspan nhất
                let mainTable = document.querySelector('table.table');
                if (!mainTable) {
                    const tables = document.querySelectorAll('table');
                    let maxRowspan = 0;
                    for (const t of tables) {
                        const rowspans = t.querySelectorAll('td[rowspan]');
                        if (rowspans.length > maxRowspan) {
                            maxRowspan = rowspans.length;
                            mainTable = t;
                        }
                    }
                }

                if (!mainTable) {
                    return {ok: false, error: 'Không tìm thấy bảng dữ liệu'};
                }

                const addSelector = 'a.add_tiet_so_dau_bai, a[onclick*="themChiTietSoDauBai"]';
                const textOf = (node) => String(node ? (node.textContent || '') : '')
                    .replace(/\\u00a0/g, ' ')
                    .replace(/\\s+/g, ' ')
                    .trim();

                function isHeaderMarkerRow(row) {
                    const cells = Array.from(row.querySelectorAll('td'));
                    if (cells.length < 8) return false;
                    const texts = cells.map((cell) => textOf(cell));
                    const prefix = ['1', '2', '3', '4', '5'];
                    return prefix.every((value, index) => texts[index] === value);
                }

                function findDataStartIndex(rows) {
                    for (let i = 0; i < rows.length; i++) {
                        if (isHeaderMarkerRow(rows[i])) {
                            return i + 1;
                        }
                    }

                    for (let i = 0; i < rows.length; i++) {
                        const cells = rows[i].querySelectorAll('td');
                        if (cells.length < 4) continue;
                        const firstText = textOf(cells[0]);
                        const secondText = textOf(cells[1]);
                        const firstRs = parseInt(cells[0].getAttribute('rowspan') || '0', 10);
                        const secondRs = parseInt(cells[1].getAttribute('rowspan') || '0', 10);
                        const looksLikeThu = (
                            firstRs >= 5 &&
                            (
                                /^(CN|[2-7])(\\s|$|\\n|\\/|-|\\d)/i.test(firstText) ||
                                /\\d{2}\\/\\d{2}\\/\\d{4}/.test(firstText)
                            )
                        );
                        const looksLikeBuoi = (
                            /sáng|chiều|sang|chieu/i.test(secondText) ||
                            secondRs >= 4
                        );
                        if (looksLikeThu || looksLikeBuoi) {
                            return i;
                        }
                    }

                    for (let i = 0; i < rows.length; i++) {
                        if (rows[i].querySelector(addSelector)) {
                            return i;
                        }
                    }

                    return 0;
                }

                // === Parse rows với xử lý rowspan ===
                const allRows = mainTable.querySelectorAll('tr');
                const dataRows = [];

                // Tìm data start thật sự: ưu tiên dòng marker "1..11", fallback sang
                // dòng dữ liệu đầu tiên có cột Thứ/Buổi, cuối cùng mới fallback theo dấu +.
                const dataStartIdx = findDataStartIndex(allRows);

                // State cho rowspan tracking
                let currentThu = '';
                let currentThuFull = '';
                let currentBuoi = '';
                let thuRemaining = 0;
                let buoiRemaining = 0;
                let addBtnCounter = 0;

                for (let i = dataStartIdx; i < allRows.length; i++) {
                    const cells = allRows[i].querySelectorAll('td');
                    if (cells.length < 2) continue;

                    let cellIdx = 0;
                    let thu = currentThu;
                    let thuFull = currentThuFull;
                    let buoi = currentBuoi;
                    let tiet = '';
                    let monHoc = '';
                    let ppct = '';
                    let hasData = false;
                    let hasAddBtn = false;
                    let actionCell = null;
                    let chiTietId = '';
                    let monHocId = '';
                    let phanMonId = '';
                    let tietPpctAttr = '';
                    let giaoVienIdDayCung = '';
                    let thongBao = '';
                    let redTexts = [];

                    // --- Xử lý cột Thứ (có rowspan) ---
                    if (thuRemaining <= 0 && cells[cellIdx]) {
                        const rs = parseInt(cells[cellIdx].getAttribute('rowspan') || '0');
                        if (rs > 1) {
                            thuFull = cells[cellIdx].innerText.trim();
                            // Trích xuất số thứ từ text (VD: "2" + ngày tháng)
                            const match = thuFull.match(/^(\\d+)/);
                            thu = match ? match[1] : thuFull;
                            currentThu = thu;
                            currentThuFull = thuFull;
                            thuRemaining = rs;
                            cellIdx++;
                        }
                    }

                    // --- Xử lý cột Buổi (có rowspan) ---
                    if (buoiRemaining <= 0 && cells[cellIdx]) {
                        const rs = parseInt(cells[cellIdx].getAttribute('rowspan') || '0');
                        const text = cells[cellIdx].innerText.trim();
                        const isBuoi = (
                            text.toLowerCase().includes('sáng') ||
                            text.toLowerCase().includes('chiều') ||
                            text.toLowerCase().includes('sang') ||
                            text.toLowerCase().includes('chieu') ||
                            rs >= 4
                        );
                        if (isBuoi || rs > 1) {
                            buoi = text;
                            currentBuoi = buoi;
                            buoiRemaining = rs > 0 ? rs : 5;
                            cellIdx++;
                        }
                    }

                    // --- Cột Hành động (nút ➕) ---
                    // VnEdu: <a class="add add_tiet_so_dau_bai">
                    if (cells[cellIdx]) {
                        actionCell = cells[cellIdx];
                        const btn = actionCell.querySelector(addSelector);
                        hasAddBtn = !!btn;
                        chiTietId = String(actionCell.getAttribute('chitiet_id') || '').trim();
                        monHocId = String(actionCell.getAttribute('mon_hoc_id') || '').trim();
                        phanMonId = String(actionCell.getAttribute('phan_mon_id') || '').trim();
                        tietPpctAttr = String(actionCell.getAttribute('tiet_ppct') || '').trim();
                        giaoVienIdDayCung = String(
                            actionCell.getAttribute('giaovien_id_daycung') || ''
                        ).trim();
                        thongBao = String(actionCell.getAttribute('thong_bao') || '').trim();
                        cellIdx++;
                    }

                    // --- Cột Tiết ---
                    if (cells[cellIdx]) {
                        tiet = cells[cellIdx].innerText.trim();
                        cellIdx++;
                    }

                    // --- Cột Môn học ---
                    if (cells[cellIdx]) {
                        monHoc = cells[cellIdx].innerText.trim();
                        cellIdx++;
                    }

                    // --- Cột PPCT ---
                    if (cells[cellIdx]) {
                        ppct = cells[cellIdx].innerText.trim();
                        cellIdx++;
                    }

                    redTexts = Array.from(allRows[i].querySelectorAll('span'))
                        .filter((span) => {
                            const style = String(span.getAttribute('style') || '').toLowerCase();
                            const computed = String(window.getComputedStyle(span).color || '').toLowerCase();
                            return style.includes('red')
                                || style.includes('#f00')
                                || style.includes('255, 0, 0')
                                || computed.includes('red')
                                || computed.includes('255, 0, 0');
                        })
                        .map((span) => textOf(span))
                        .filter(Boolean);
                    const hasMeaningfulValue = (value) => {
                        const normalized = String(value == null ? '' : value).trim().toLowerCase();
                        return Boolean(normalized)
                            && !['0', 'false', 'null', 'undefined', 'none'].includes(normalized);
                    };
                    const hasSuggestionMetadata = Boolean(
                        hasMeaningfulValue(monHocId)
                        || hasMeaningfulValue(phanMonId)
                        || hasMeaningfulValue(tietPpctAttr)
                        || hasMeaningfulValue(giaoVienIdDayCung)
                        || redTexts.length
                    );
                    hasData = Boolean(chiTietId)
                        || (monHoc.length > 0 && !hasAddBtn && !hasSuggestionMetadata);
                    const isScheduled = !hasData && Boolean(
                        hasSuggestionMetadata || monHoc || ppct || hasMeaningfulValue(thongBao)
                    );
                    const isUnplanned = !hasData && !isScheduled;

                    // Giảm counter rowspan
                    thuRemaining--;
                    buoiRemaining--;

                    // Chỉ thêm row nếu có tiết (bỏ qua header/footer/summary rows)
                    if (tiet && /^\\d+$/.test(tiet)) {
                        const addBtnIndex = hasAddBtn ? addBtnCounter : -1;
                        if (hasAddBtn) {
                            addBtnCounter++;
                        }
                        dataRows.push({
                            index: dataRows.length,
                            rowIdx: i,
                            thu: thu,
                            thu_full: thuFull,
                            buoi: buoi,
                            tiet: tiet,
                            mon_hoc: monHoc,
                            ppct: ppct,
                            has_data: hasData,
                            is_scheduled: isScheduled,
                            is_unplanned: isUnplanned,
                            has_add_btn: hasAddBtn,
                            add_btn_index: addBtnIndex,
                            chitiet_id: chiTietId,
                            mon_hoc_id: monHocId,
                            phan_mon_id: phanMonId,
                            tiet_ppct_attr: tietPpctAttr,
                            giaovien_id_daycung: giaoVienIdDayCung,
                            thong_bao: thongBao,
                            ppct_hint: tietPpctAttr || ppct,
                            mon_hoc_hint: monHoc,
                            red_texts: redTexts,
                        });
                    }
                }

                return {
                    ok: true,
                    rows: dataRows,
                    total: dataRows.length,
                    tableId: mainTable.id || '',
                    tableClass: (mainTable.className || '').substring(0, 50)
                };
            }''')

            if result.get("ok"):
                rows = result.get("rows", [])
                logger.info(f"Read table: {len(rows)} rows")
                return True, rows
            else:
                return False, result.get("error", "Unknown error")

        except PlaywrightTimeout:
            return False, "Timeout đọc bảng (page chưa load xong?)"
        except Exception as e:
            return False, f"Lỗi đọc bảng: {type(e).__name__}: {str(e)[:80]}"

    def get_empty_rows(self):
        """Lấy danh sách tiết chưa nhập (có nút ➕, chưa có dữ liệu).

        Returns:
            (success, list[dict]) — filtered rows chưa có dữ liệu
        """
        ok, data = self.read_table()
        if not ok:
            return False, data
        empty = [r for r in data if r.get("has_add_btn") and not r.get("has_data")]
        return True, empty

    def set_goi_y_khdh_mode(self, enabled=True):
        """Bật/tắt chế độ Gợi ý theo KHDH trên toolbar Sổ đầu bài.

        Ưu tiên thao tác đúng control ExtJS/radio hiện có trên trang. Fallback
        sang click DOM label chứa text "Gợi ý theo KHDH" nếu component query
        không ổn định.

        Returns:
            (success: bool, message: str)
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        target_enabled = bool(enabled)
        try:
            result = self.page.evaluate(
                '''(targetEnabled) => {
                    const normalize = (value) => {
                        try {
                            return String(value == null ? '' : value)
                                .normalize('NFD')
                                .replace(/[\\u0300-\\u036f]/g, '')
                                .replace(/\\s+/g, ' ')
                                .trim()
                                .toLowerCase();
                        } catch (e) {
                            return String(value == null ? '' : value)
                                .replace(/\\s+/g, ' ')
                                .trim()
                                .toLowerCase();
                        }
                    };

                    const targetRootId = targetEnabled ? 'rdoEdit' : 'rdoView';
                    const targetTextToken = targetEnabled ? 'goi y theo khdh' : 'xem';

                    const readCheckedFromDom = (rootId) => {
                        const root = document.querySelector('#' + rootId);
                        if (!root) return false;
                        const cls = String(root.className || '');
                        if (cls.indexOf('x-form-cb-checked') >= 0) return true;
                        const radio = root.querySelector('input[type="radio"], input[type="checkbox"]');
                        return !!(radio && radio.checked);
                    };

                    const clickDomControl = () => {
                        const root = document.querySelector('#' + targetRootId);
                        if (root) {
                            const label = root.querySelector('label');
                            if (label) {
                                label.click();
                                return true;
                            }
                            const input = root.querySelector('input[type="radio"], input[type="checkbox"]');
                            if (input) {
                                input.click();
                                return true;
                            }
                            root.click();
                            return true;
                        }

                        const nodes = Array.from(document.querySelectorAll('label, span, div'));
                        for (const node of nodes) {
                            const text = normalize(node.innerText || node.textContent || '');
                            if (text === targetTextToken || text.includes(targetTextToken)) {
                                node.click();
                                return true;
                            }
                        }
                        return false;
                    };

                    const readCheckedFromExt = () => {
                        if (!(window.Ext && Ext.ComponentQuery)) return null;
                        try {
                            const candidates = [];
                            const radios = Ext.ComponentQuery.query('radiofield');
                            const checks = Ext.ComponentQuery.query('checkboxfield');
                            candidates.push.apply(candidates, radios);
                            candidates.push.apply(candidates, checks);
                            for (const field of candidates) {
                                let fieldId = '';
                                let fieldLabel = '';
                                let boxLabel = '';
                                let name = '';
                                try { fieldId = String(field.id || ''); } catch (e) {}
                                try { fieldLabel = String(field.fieldLabel || ''); } catch (e) {}
                                try { boxLabel = String(field.boxLabel || ''); } catch (e) {}
                                try { name = String(field.getName ? field.getName() : (field.name || '')); } catch (e) {}
                                const haystack = normalize([fieldId, fieldLabel, boxLabel, name].join(' '));
                                if (!haystack) continue;
                                const isTarget = targetEnabled
                                    ? (haystack.includes('rdoedit') || haystack.includes('goi y theo khdh'))
                                    : (haystack.includes('rdoview') || haystack === 'xem' || haystack.includes(' xem '));
                                if (isTarget) {
                                    let checked = false;
                                    try { checked = !!(field.getValue ? field.getValue() : field.checked); } catch (e2) {}
                                    return {field, checked};
                                }
                            }
                        } catch (e3) {}
                        return null;
                    };

                    const extMatch = readCheckedFromExt();
                    if (extMatch && extMatch.checked) {
                        return {ok: true, checked: true, method: 'ext_state'};
                    }
                    if (!extMatch && readCheckedFromDom(targetRootId)) {
                        return {ok: true, checked: true, method: 'dom_state'};
                    }

                    if (extMatch) {
                        try {
                            if (extMatch.field.setValue) extMatch.field.setValue(true);
                            if (extMatch.field.fireEvent) {
                                extMatch.field.fireEvent('change', extMatch.field, true);
                                extMatch.field.fireEvent('select', extMatch.field, true);
                            }
                        } catch (extErr) {}
                    } else {
                        const clicked = clickDomControl();
                        if (!clicked) {
                            return {ok: false, error: 'Không tìm thấy control ' + (targetEnabled ? 'Gợi ý theo KHDH' : 'Xem')};
                        }
                    }

                    const checked = readCheckedFromExt()
                        ? !!readCheckedFromExt().checked
                        : readCheckedFromDom(targetRootId);
                    if (!checked) {
                        return {
                            ok: false,
                            error: 'Không đổi được trạng thái ' + (targetEnabled ? 'Gợi ý theo KHDH' : 'Xem'),
                            checked: checked
                        };
                    }

                    return {ok: true, checked: checked, method: extMatch ? 'ext_set' : 'dom_click'};
                }''',
                target_enabled,
            )
            if result.get("ok"):
                return True, "Đã bật Gợi ý theo KHDH" if target_enabled else "Đã chuyển về Xem"
            return False, result.get("error", "Không đổi được trạng thái Gợi ý theo KHDH")
        except Exception as e:
            return False, f"Lỗi đổi mode Gợi ý theo KHDH: {type(e).__name__}: {str(e)[:120]}"

    def cleanup_after_automation(self, restore_view_mode=False):
        """Đưa trang Sổ đầu bài về trạng thái có thể thao tác sau automation.

        Cleanup này chỉ chạy trong tab VnEdu hiện tại: đóng popup nhập liệu còn sót,
        collapse combobox/boundlist, chờ AJAX/loadmask lắng xuống và trả về mode Xem
        nếu worker trước đó bật Gợi ý theo KHDH.
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        notes = []
        try:
            try:
                ok_close, msg_close = self.close_form()
                notes.append(f"close_form={ok_close}:{msg_close}")
                self.wait_for_lesson_form_closed(timeout_s=1.2, poll_interval=0.08)
            except Exception as e_close:
                notes.append(f"close_form_error={type(e_close).__name__}")

            if restore_view_mode:
                ok_view, msg_view = self.set_goi_y_khdh_mode(False)
                notes.append(f"view_mode={ok_view}:{msg_view}")

            self._wait_page_update(timeout_s=3.0)

            result = self.page.evaluate('''() => {
                const out = {
                    combo_collapsed: 0,
                    masks_hidden: 0,
                    visible_masks_before: 0,
                    visible_masks_after: 0,
                    ajax_loading: false,
                    visible_windows: 0,
                    blocking_windows: 0,
                };
                const normalize = (value) => {
                    try {
                        return String(value == null ? '' : value)
                            .normalize('NFD')
                            .replace(/[\\u0300-\\u036f]/g, '')
                            .replace(/\\s+/g, ' ')
                            .trim()
                            .toLowerCase();
                    } catch (e) {
                        return String(value == null ? '' : value)
                            .replace(/\\s+/g, ' ')
                            .trim()
                            .toLowerCase();
                    }
                };
                const isVisible = (el) => {
                    if (!el) return false;
                    const style = window.getComputedStyle ? window.getComputedStyle(el) : null;
                    const rect = el.getBoundingClientRect ? el.getBoundingClientRect() : {width: 0, height: 0};
                    return !!(
                        (!style || (style.display !== 'none' && style.visibility !== 'hidden' && parseFloat(style.opacity || '1') > 0)) &&
                        rect.width > 0 &&
                        rect.height > 0
                    );
                };
                const getWindowTitle = (win) => {
                    try {
                        if (win && win.title) return String(win.title || '');
                    } catch (e) {}
                    try {
                        if (win && win.header && win.header.titleCmp && win.header.titleCmp.text) {
                            return String(win.header.titleCmp.text || '');
                        }
                    } catch (e2) {}
                    try {
                        const dom = win && win.el && win.el.dom ? win.el.dom : null;
                        if (dom) {
                            const titleNode = dom.querySelector('.x-window-header-text, .x-title-text, .x-window-header');
                            if (titleNode) return String(titleNode.innerText || titleNode.textContent || '');
                        }
                    } catch (e3) {}
                    return '';
                };

                try {
                    if (window.Ext && Ext.ComponentQuery) {
                        const combos = Ext.ComponentQuery.query('combobox');
                        for (const combo of combos) {
                            try {
                                if (combo.collapse) {
                                    combo.collapse();
                                    out.combo_collapsed += 1;
                                }
                            } catch (eCombo) {}
                        }
                        const wins = Ext.ComponentQuery.query('window');
                        for (const win of wins) {
                            try {
                                if (!(win.isVisible && win.isVisible())) continue;
                                out.visible_windows += 1;
                                const title = normalize(getWindowTitle(win));
                                const isMainSdbWindow = title.includes('quan ly so dau bai');
                                if (!isMainSdbWindow) out.blocking_windows += 1;
                            } catch (eWin) {}
                        }
                    }
                } catch (eExt) {}

                try {
                    out.ajax_loading = !!(window.Ext && Ext.Ajax && Ext.Ajax.isLoading && Ext.Ajax.isLoading());
                } catch (eAjax) {}

                const masks = Array.from(document.querySelectorAll('.x-mask-msg, .x-mask-loading, .x-mask'));
                out.visible_masks_before = masks.filter(isVisible).length;

                // Nếu không còn AJAX/window nào mà mask vẫn hiện, đó thường là mask mồ côi
                // sau reload/close popup; ẩn nó để trả lại khả năng click cho người dùng.
                // Bỏ qua cửa sổ chính "Quản lý sổ đầu bài"; nó luôn tồn tại và không phải
                // popup chặn thao tác.
                if (!out.ajax_loading && out.blocking_windows === 0 && out.visible_masks_before > 0) {
                    for (const mask of masks) {
                        try {
                            if (!isVisible(mask)) continue;
                            mask.setAttribute('data-auto-sdb-hidden-orphan-mask', '1');
                            mask.style.display = 'none';
                            mask.style.visibility = 'hidden';
                            mask.style.pointerEvents = 'none';
                            out.masks_hidden += 1;
                        } catch (eMask) {}
                    }
                }
                out.visible_masks_after = masks.filter(isVisible).length;
                return out;
            }''')
            notes.append(f"dom_cleanup={result}")
            self._wait_page_update(timeout_s=2.0)
            return True, " | ".join(notes)
        except Exception as e:
            return False, f"Lỗi cleanup sau automation: {type(e).__name__}: {str(e)[:120]}"

    def diagnose_ui_state(self):
        """Đọc trạng thái thao tác chính của trang VNEDU sau automation.

        Dùng cho log và nút khôi phục: radio Xem/KHDH, dropdown, AJAX, mask,
        popup đang mở. Không sửa dữ liệu trên trang.
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        try:
            result = self.page.evaluate('''() => {
                const normalize = (value) => {
                    try {
                        return String(value == null ? '' : value)
                            .normalize('NFD')
                            .replace(/[\\u0300-\\u036f]/g, '')
                            .replace(/\\s+/g, ' ')
                            .trim()
                            .toLowerCase();
                    } catch (e) {
                        return String(value == null ? '' : value)
                            .replace(/\\s+/g, ' ')
                            .trim()
                            .toLowerCase();
                    }
                };
                const isVisible = (el) => {
                    if (!el) return false;
                    const style = window.getComputedStyle ? window.getComputedStyle(el) : null;
                    const rect = el.getBoundingClientRect ? el.getBoundingClientRect() : {width: 0, height: 0};
                    return !!(
                        (!style || (style.display !== 'none' && style.visibility !== 'hidden' && parseFloat(style.opacity || '1') > 0)) &&
                        rect.width > 0 &&
                        rect.height > 0
                    );
                };
                const readRadioChecked = (rootId) => {
                    const root = document.querySelector('#' + rootId);
                    if (!root) return null;
                    const cls = String(root.className || '');
                    if (cls.includes('x-form-cb-checked')) return true;
                    const input = root.querySelector('input[type="radio"], input[type="checkbox"]');
                    if (input) return !!input.checked;
                    return false;
                };
                const getWindowTitle = (win) => {
                    try {
                        if (win && win.title) return String(win.title || '');
                    } catch (e) {}
                    try {
                        if (win && win.header && win.header.titleCmp && win.header.titleCmp.text) {
                            return String(win.header.titleCmp.text || '');
                        }
                    } catch (e2) {}
                    try {
                        const dom = win && win.el && win.el.dom ? win.el.dom : null;
                        if (dom) {
                            const titleNode = dom.querySelector('.x-window-header-text, .x-title-text, .x-window-header');
                            if (titleNode) return String(titleNode.innerText || titleNode.textContent || '');
                        }
                    } catch (e3) {}
                    return '';
                };

                const out = {
                    url: location.href,
                    title: document.title || '',
                    has_ext: !!(window.Ext && Ext.ComponentQuery),
                    ajax_loading: false,
                    view_checked: readRadioChecked('rdoView'),
                    khdh_checked: readRadioChecked('rdoEdit'),
                    visible_masks: 0,
                    visible_boundlists: 0,
                    visible_windows: 0,
                    blocking_windows: 0,
                    combos: [],
                    has_sdb_table: !!document.querySelector('table.table, a.add_tiet_so_dau_bai'),
                };

                try {
                    out.ajax_loading = !!(window.Ext && Ext.Ajax && Ext.Ajax.isLoading && Ext.Ajax.isLoading());
                } catch (eAjax) {}

                try {
                    const masks = Array.from(document.querySelectorAll('.x-mask-msg, .x-mask-loading, .x-mask'));
                    out.visible_masks = masks.filter(isVisible).length;
                } catch (eMask) {}

                try {
                    out.visible_boundlists = Array.from(document.querySelectorAll('.x-boundlist, .x-combo-list'))
                        .filter(isVisible).length;
                } catch (eList) {}

                if (window.Ext && Ext.ComponentQuery) {
                    try {
                        const combos = Ext.ComponentQuery.query('combobox');
                        for (const combo of combos) {
                            let name = '';
                            let raw = '';
                            let storeCount = null;
                            try { name = combo.getName ? combo.getName() : (combo.name || ''); } catch (e) {}
                            try { raw = combo.getRawValue ? combo.getRawValue() : (combo.rawValue || ''); } catch (e2) {}
                            try { storeCount = combo.store && combo.store.getCount ? combo.store.getCount() : null; } catch (e3) {}
                            if (['cboCapHoc', 'cboTuanHoc', 'cboLopHoc'].includes(name)) {
                                out.combos.push({
                                    id: combo.id || '',
                                    name,
                                    raw: String(raw || ''),
                                    disabled: !!combo.disabled,
                                    readOnly: !!combo.readOnly,
                                    expanded: !!combo.isExpanded,
                                    storeCount,
                                });
                            }
                        }
                    } catch (eCombo) {}

                    try {
                        const wins = Ext.ComponentQuery.query('window');
                        for (const win of wins) {
                            try {
                                if (!(win.isVisible && win.isVisible())) continue;
                                out.visible_windows += 1;
                                const title = normalize(getWindowTitle(win));
                                if (!title.includes('quan ly so dau bai')) out.blocking_windows += 1;
                            } catch (eWin) {}
                        }
                    } catch (eWinOuter) {}
                }
                return out;
            }''')
            return True, result
        except Exception as e:
            return False, f"Lỗi đọc trạng thái UI: {type(e).__name__}: {str(e)[:120]}"

    def probe_khdh_schedule_context(self):
        """Kiểm tra nhanh trang live hiện tại có đủ control cho mode KHDH hay không."""
        if not self.is_connected:
            return False, "Chưa kết nối CDP", {}

        try:
            result = self.page.evaluate(
                '''() => {
                    const hasExt = !!(window.Ext && Ext.ComponentQuery);
                    const hasKhdhToggle = !!document.querySelector('#rdoEdit');
                    const hasWeek = !!document.querySelector('#cboTuanHoc, input[name="cboTuanHoc"]');
                    const hasClass = !!document.querySelector('#cboLopHoc, input[name="cboLopHoc"]');
                    const hasTable = !!document.querySelector('table.table, table');
                    let title = '';
                    try { title = String(document.title || ''); } catch (e) {}
                    return {
                        ok: hasExt && hasKhdhToggle && hasWeek && hasClass && hasTable,
                        title,
                        hasExt,
                        hasKhdhToggle,
                        hasWeek,
                        hasClass,
                        hasTable,
                    };
                }'''
            )
            if result.get("ok"):
                return True, "Trang live đã sẵn sàng cho mode KHDH", result
            missing = []
            if not result.get("hasExt"):
                missing.append("ExtJS")
            if not result.get("hasKhdhToggle"):
                missing.append("nút Gợi ý theo KHDH")
            if not result.get("hasWeek"):
                missing.append("dropdown Tuần")
            if not result.get("hasClass"):
                missing.append("dropdown Lớp")
            if not result.get("hasTable"):
                missing.append("bảng Sổ đầu bài")
            detail = ", ".join(missing) if missing else "context cần thiết"
            return False, f"Trang live chưa sẵn sàng cho KHDH: thiếu {detail}", result
        except Exception as e:
            return False, f"Lỗi kiểm tra context KHDH: {type(e).__name__}: {str(e)[:120]}", {}

    def read_khdh_suggested_rows(self):
        """Đọc các row gợi ý theo KHDH đang hiển thị bằng chữ đỏ trên bảng live.

        Mỗi row trả về đủ metadata để worker có thể click đúng nút `+` của
        chính row đó và verify popup sau khi mở.

        Returns:
            (success, rows|message)
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        try:
            result = self.page.evaluate(
                '''() => {
                    function textOf(node) {
                        return String(node ? (node.textContent || '') : '')
                            .replace(/\\u00a0/g, ' ')
                            .replace(/\\s+/g, ' ')
                            .trim();
                    }

                    function normalizeBuoi(raw) {
                        const value = String(raw || '').trim();
                        if (!value) return '';
                        if (value === '1') return 'Sáng';
                        if (value === '2') return 'Chiều';
                        return value;
                    }

                    let mainTable = document.querySelector('table.table');
                    if (!mainTable) {
                        const tables = document.querySelectorAll('table');
                        let maxRowspan = 0;
                        for (const table of tables) {
                            const rowspans = table.querySelectorAll('td[rowspan]');
                            if (rowspans.length > maxRowspan) {
                                maxRowspan = rowspans.length;
                                mainTable = table;
                            }
                        }
                    }
                    if (!mainTable) {
                        return {ok: false, error: 'Không tìm thấy bảng Sổ đầu bài'};
                    }

                    const rows = Array.from(mainTable.querySelectorAll('tr'));
                    const addSelector = 'a.add_tiet_so_dau_bai, a[onclick*="themChiTietSoDauBai"]';
                    const items = [];
                    let currentThu = '';
                    let currentBuoi = '';
                    let currentDate = '';
                    let addBtnIndex = 0;

                    for (let rowIdx = 0; rowIdx < rows.length; rowIdx++) {
                        const tr = rows[rowIdx];
                        const firstActionCell = tr.querySelector('td[thu][tiet]');
                        if (firstActionCell) {
                            const attrThu = String(firstActionCell.getAttribute('thu') || '').trim();
                            if (attrThu) currentThu = attrThu === 'CN' ? '8' : attrThu;
                            const attrBuoi = String(firstActionCell.getAttribute('buoi') || '').trim();
                            if (attrBuoi) currentBuoi = normalizeBuoi(attrBuoi);
                        }

                        const dateNode = tr.querySelector('div[class^="thu"]');
                        if (dateNode) {
                            const dateText = textOf(dateNode);
                            if (dateText) currentDate = dateText;
                        }

                        const buoiCell = Array.from(tr.querySelectorAll('td[rowspan]')).find(
                            (td) => /sáng|chiều/i.test(textOf(td))
                        );
                        if (buoiCell) {
                            const buoiText = textOf(buoiCell);
                            if (buoiText) currentBuoi = buoiText;
                        }

                        const addLink = tr.querySelector(addSelector);
                        const currentAddBtnIndex = addLink ? addBtnIndex : null;
                        if (addLink) addBtnIndex += 1;

                        const redSpans = Array.from(tr.querySelectorAll('span')).filter((el) => {
                            const color = String(window.getComputedStyle(el).color || '').toLowerCase();
                            return color.includes('255, 0, 0') || color.includes('red');
                        });
                        if (!redSpans.length) continue;

                        const actionCell = firstActionCell || tr.querySelector('td[thu][tiet], td[tiet]');
                        const tietAttr = actionCell ? String(actionCell.getAttribute('tiet') || '').trim() : '';
                        const thuAttrRaw = actionCell ? String(actionCell.getAttribute('thu') || '').trim() : '';
                        const thuAttr = thuAttrRaw === 'CN' ? '8' : thuAttrRaw;
                        const buoiAttr = actionCell ? String(actionCell.getAttribute('buoi') || '').trim() : '';
                        const monHocId = actionCell ? String(actionCell.getAttribute('mon_hoc_id') || '').trim() : '';
                        const phanMonId = actionCell ? String(actionCell.getAttribute('phan_mon_id') || '').trim() : '';
                        const tietPpctAttr = actionCell ? String(actionCell.getAttribute('tiet_ppct') || '').trim() : '';
                        const monHocTextAttr = actionCell
                            ? String(
                                actionCell.getAttribute('ten_mon_hoc') ||
                                actionCell.getAttribute('mon_hoc') ||
                                actionCell.getAttribute('mon_hoc_text') ||
                                ''
                            ).trim()
                            : '';
                        const phanMonTextAttr = actionCell
                            ? String(
                                actionCell.getAttribute('ten_phan_mon') ||
                                actionCell.getAttribute('phan_mon') ||
                                actionCell.getAttribute('phan_mon_text') ||
                                ''
                            ).trim()
                            : '';

                        const cells = Array.from(tr.querySelectorAll('td'));
                        const texts = cells.map((td) => textOf(td)).filter(Boolean);
                        const tietText = texts.find((value) => /^\\d+$/.test(value)) || tietAttr;
                        const redTexts = redSpans.map((span) => textOf(span)).filter(Boolean);
                        const monHocHint = monHocTextAttr || redTexts[0] || '';
                        const ppctHint = tietPpctAttr || redTexts.find((value) => /^\\d+$/.test(value)) || '';
                        const noiDungHint = redTexts.length >= 2 ? redTexts[redTexts.length - 1] : '';

                        items.push({
                            rowIdx: rowIdx,
                            add_btn_index: currentAddBtnIndex,
                            thu: thuAttr || currentThu,
                            buoi: normalizeBuoi(buoiAttr || currentBuoi),
                            tiet: tietAttr || tietText,
                            ngay: currentDate,
                            mon_hoc_id: monHocId,
                            phan_mon_id: phanMonId,
                            ppct_hint: ppctHint,
                            mon_hoc_hint: monHocHint,
                            mon_hoc_text_hint: monHocTextAttr || monHocHint,
                            phan_mon_text_hint: phanMonTextAttr,
                            noi_dung_hint: noiDungHint,
                            red_texts: redTexts,
                            row_texts: texts,
                            has_add_btn: !!addLink,
                        });
                    }

                    return {ok: true, rows: items};
                }'''
            )
            if result.get("ok"):
                rows = result.get("rows", [])
                logger.info(f"KHDH rows: {len(rows)} suggested rows")
                return True, rows
            return False, result.get("error", "Không đọc được các row KHDH")
        except Exception as e:
            return False, f"Lỗi đọc row KHDH: {type(e).__name__}: {str(e)[:120]}"

    def discover_delete_controls(self, max_rows=8):
        """[READ-ONLY] Dump cấu trúc nút action trên các dòng đã có dữ liệu.

        Mục đích: tìm chính xác selector/onclick/href của nút XÓA (icon tròn
        đỏ dấu trừ) trước khi implement chức năng xóa. KHÔNG click, KHÔNG xóa.

        Trả về thông tin từng dòng có dữ liệu:
            - các <a>/<button>/<i>/<span> trong cột hành động (2 cột đầu)
            - tagName, class, onclick (rút gọn), href, title, màu chữ
        Dùng để con người + AI xác định selector nút xóa thật.

        Returns:
            (success, dict|message)
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        try:
            result = self.page.evaluate(
                '''(maxRows) => {
                    function textOf(node) {
                        return String(node ? (node.textContent || '') : '')
                            .replace(/\\u00a0/g, ' ')
                            .replace(/\\s+/g, ' ')
                            .trim();
                    }

                    function describeControl(el) {
                        if (!el) return null;
                        let onclickAttr = '';
                        try {
                            onclickAttr = String(el.getAttribute('onclick') || '');
                        } catch (e) {}
                        let color = '';
                        try {
                            color = String(window.getComputedStyle(el).color || '');
                        } catch (e) {}
                        let bgColor = '';
                        try {
                            bgColor = String(window.getComputedStyle(el).backgroundColor || '');
                        } catch (e) {}
                        const dataAttrs = {};
                        try {
                            for (const attr of Array.from(el.attributes || [])) {
                                const name = String(attr.name || '');
                                if (
                                    name.startsWith('data-') ||
                                    ['thu', 'buoi', 'tiet', 'mon_hoc_id', 'phan_mon_id',
                                     'tiet_ppct', 'id', 'so_dau_bai_id', 'chi_tiet_id'].includes(name)
                                ) {
                                    dataAttrs[name] = String(attr.value || '');
                                }
                            }
                        } catch (e) {}
                        return {
                            tag: String(el.tagName || ''),
                            className: String(el.className || ''),
                            id: String(el.id || ''),
                            title: String(el.getAttribute && el.getAttribute('title') || ''),
                            onclick: onclickAttr.slice(0, 200),
                            href: String(el.getAttribute && el.getAttribute('href') || '').slice(0, 120),
                            text: textOf(el).slice(0, 40),
                            color: color,
                            backgroundColor: bgColor,
                            dataAttrs: dataAttrs,
                            innerHTML: String(el.innerHTML || '').slice(0, 160),
                        };
                    }

                    let mainTable = document.querySelector('table.table');
                    if (!mainTable) {
                        const tables = document.querySelectorAll('table');
                        let maxRowspan = 0;
                        for (const t of tables) {
                            const rowspans = t.querySelectorAll('td[rowspan]');
                            if (rowspans.length > maxRowspan) {
                                maxRowspan = rowspans.length;
                                mainTable = t;
                            }
                        }
                    }
                    if (!mainTable) {
                        return {ok: false, error: 'Không tìm thấy bảng dữ liệu'};
                    }

                    const rows = Array.from(mainTable.querySelectorAll('tr'));
                    const out = [];
                    const globalSelectors = {};

                    // Đếm class phổ biến của <a>/<i> trong toàn bảng để tìm pattern xóa
                    const allActionEls = Array.from(
                        mainTable.querySelectorAll('a, button, i, span[onclick], img')
                    );
                    for (const el of allActionEls) {
                        const cls = String(el.className || '').trim();
                        let onclickAttr = '';
                        try { onclickAttr = String(el.getAttribute('onclick') || ''); } catch (e) {}
                        const keyParts = [];
                        if (cls) keyParts.push('class=' + cls);
                        const onclickFn = onclickAttr.match(/([a-zA-Z_$][\\w$]*)\\s*\\(/);
                        if (onclickFn) keyParts.push('fn=' + onclickFn[1]);
                        const key = keyParts.join(' | ');
                        if (!key) continue;
                        globalSelectors[key] = (globalSelectors[key] || 0) + 1;
                    }

                    for (let i = 0; i < rows.length && out.length < maxRows; i++) {
                        const tr = rows[i];
                        const cells = tr.querySelectorAll('td');
                        if (cells.length < 4) continue;

                        // Chỉ quan tâm dòng đã có dữ liệu (có action cell với attr thu/tiet
                        // hoặc có nút action bất kỳ)
                        const actionEls = Array.from(
                            tr.querySelectorAll('a, button, i, span[onclick], img')
                        );
                        if (actionEls.length === 0) continue;

                        const rowTexts = Array.from(cells).map((c) => textOf(c)).filter(Boolean);
                        const controls = actionEls
                            .map(describeControl)
                            .filter(Boolean);

                        // Lấy attr từ action cell đầu tiên có thu/tiet
                        let actionCellAttrs = {};
                        const actionCell = tr.querySelector('td[thu][tiet], td[tiet]');
                        if (actionCell) {
                            try {
                                for (const attr of Array.from(actionCell.attributes || [])) {
                                    actionCellAttrs[String(attr.name)] = String(attr.value || '');
                                }
                            } catch (e) {}
                        }

                        out.push({
                            rowIdx: i,
                            row_texts: rowTexts.slice(0, 12),
                            action_cell_attrs: actionCellAttrs,
                            controls: controls,
                        });
                    }

                    return {
                        ok: true,
                        table_id: mainTable.id || '',
                        table_class: String(mainTable.className || '').slice(0, 60),
                        global_selectors: globalSelectors,
                        rows: out,
                    };
                }''',
                int(max_rows),
            )
            if result.get("ok"):
                return True, result
            return False, result.get("error", "Không dump được control xóa")
        except Exception as e:
            return False, f"Lỗi discover delete controls: {type(e).__name__}: {str(e)[:120]}"

    def fetch_deletable_entries(self, lop_text, tuan_num, timeout_s=12.0):
        """Fetch HTML sổ đầu bài và trích các tiết ĐÃ CÓ dữ liệu để xóa.

        Mỗi entry chứa chitiet_id thật (lấy từ <td chitiet_id="...">) cùng
        metadata thu/buổi/tiết/môn/PPCT để preview và verify. Chỉ lấy ô có
        chitiet_id non-empty (tức là tiết đã ghi dữ liệu).

        Returns:
            (success, payload|message)
            payload = {
                "entries": [
                    {chitiet_id, thu, thu_full, buoi, tiet, mon_hoc, ppct,
                     noi_dung, nhan_xet, ky_ten},
                    ...
                ],
                "week": int|None,
                "lop": str,
                "fetch_ms": int,
            }
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        timeout_ms = max(int(timeout_s * 1000), 3000)
        try:
            result = self.page.evaluate(
                '''async (args) => {
                    function normalize(value) {
                        try {
                            return String(value == null ? '' : value)
                                .normalize('NFD')
                                .replace(/[\\u0300-\\u036f]/g, '')
                                .replace(/\\s+/g, ' ')
                                .trim()
                                .toLowerCase();
                        } catch (e) {
                            return String(value == null ? '' : value)
                                .replace(/\\s+/g, ' ')
                                .trim()
                                .toLowerCase();
                        }
                    }

                    function getCombo(name) {
                        if (typeof Ext === 'undefined' || !Ext.ComponentQuery) return null;
                        for (const combo of Ext.ComponentQuery.query('combobox')) {
                            try {
                                const comboName = combo.getName ? combo.getName() : (combo.name || '');
                                if (comboName === name) return combo;
                            } catch (e) {}
                        }
                        return null;
                    }

                    function getStoreItems(store) {
                        if (!store) return [];
                        if (store.data && Array.isArray(store.data.items)) {
                            return store.data.items;
                        }
                        return [];
                    }

                    function getRecordValue(record, fieldName) {
                        if (!record) return '';
                        try {
                            if (record.get && typeof record.get === 'function') {
                                return record.get(fieldName);
                            }
                        } catch (e) {}
                        try {
                            return record.data ? record.data[fieldName] : '';
                        } catch (e2) {
                            return '';
                        }
                    }

                    function textOf(node) {
                        return String(node ? (node.textContent || '') : '')
                            .replace(/\\u00a0/g, ' ')
                            .replace(/\\s+/g, ' ')
                            .trim();
                    }

                    function normalizeBuoi(raw) {
                        const value = String(raw || '').trim();
                        if (value === '1') return 'Sáng';
                        if (value === '2') return 'Chiều';
                        return value;
                    }

                    function normalizeThu(raw) {
                        const value = String(raw || '').trim().toUpperCase();
                        if (value === 'CN' || value === '8') return 'CN';
                        return value;
                    }

                    if (typeof Ext === 'undefined' || !Ext.ComponentQuery) {
                        return {ok: false, error: 'ExtJS not available'};
                    }

                    const capCombo = getCombo('cboCapHoc');
                    const lopCombo = getCombo('cboLopHoc');
                    if (!lopCombo || !lopCombo.store) {
                        return {ok: false, error: 'Không tìm thấy combobox lớp hoặc store lớp'};
                    }

                    const lopDisplayField = lopCombo.displayField || 'ten';
                    const lopValueField = lopCombo.valueField || 'id';
                    const lopItems = getStoreItems(lopCombo.store);
                    const targetLop = normalize(args.lopText);
                    let classRec = null;
                    for (const rec of lopItems) {
                        if (normalize(getRecordValue(rec, lopDisplayField)) === targetLop) {
                            classRec = rec;
                            break;
                        }
                    }
                    if (!classRec) {
                        return {ok: false, error: 'Không tìm thấy lớp ' + args.lopText + ' trong store hiện tại'};
                    }

                    const token = typeof myToken !== 'undefined'
                        ? (typeof myToken === 'function' ? myToken() : myToken) : '';
                    const userId = typeof myUserId !== 'undefined'
                        ? (typeof myUserId === 'function' ? myUserId() : myUserId) : '';
                    const namHoc = String(
                        typeof phpviet_nam_hoc_v5 !== 'undefined' ? phpviet_nam_hoc_v5 : 2025
                    );
                    const capHoc = capCombo
                        ? (capCombo.getValue ? capCombo.getValue() : capCombo.value)
                        : (getRecordValue(classRec, 'cap') || 2);
                    const khoiHoc = getRecordValue(classRec, 'khoi') || '';
                    const classId = String(getRecordValue(classRec, lopValueField) || '');

                    const params = new URLSearchParams();
                    params.set('my_token', String(token || ''));
                    params.set('my_user_id', String(userId || ''));
                    params.set('app_nam_hoc', namHoc);
                    params.set('capHoc', String(capHoc || ''));
                    params.set('lopHoc', classId);
                    params.set('khoiHoc', String(khoiHoc || ''));
                    params.set('tuanHoc', String(args.tuanNum));
                    params.set('show_goi_y', '0');

                    const url = './?load=app.sodaubai.serv.so_dau_bai'
                        + '&app_nam_hoc=' + encodeURIComponent(namHoc)
                        + '&my_token=' + encodeURIComponent(String(token || ''));

                    const t0 = performance.now();
                    const timeoutMs = Math.max(parseInt(args.timeoutMs || 0, 10) || 0, 3000);
                    const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
                    let timeoutHandle = null;
                    if (controller) {
                        timeoutHandle = setTimeout(() => {
                            try { controller.abort(); } catch (e) {}
                        }, timeoutMs);
                    }

                    let resp = null;
                    let html = '';
                    try {
                        resp = await fetch(url, {
                            method: 'POST',
                            headers: {'content-type': 'application/x-www-form-urlencoded; charset=UTF-8'},
                            body: params.toString(),
                            credentials: 'same-origin',
                            signal: controller ? controller.signal : undefined,
                        });
                        html = await resp.text();
                    } catch (error) {
                        const errorName = String(error && error.name ? error.name : '');
                        return {
                            ok: false,
                            error: errorName === 'AbortError'
                                ? ('Timeout fetch service sau ' + timeoutMs + 'ms')
                                : ('Lỗi fetch service: ' + String(error && error.message ? error.message : error)),
                        };
                    } finally {
                        if (timeoutHandle) clearTimeout(timeoutHandle);
                    }
                    const fetchMs = Math.round(performance.now() - t0);

                    if (!resp.ok) {
                        return {ok: false, error: 'Fetch service thất bại: HTTP ' + resp.status, fetch_ms: fetchMs};
                    }

                    const parsed = new DOMParser().parseFromString(html, 'text/html');

                    // Lấy ngày theo thứ (div.thu2, div.thu3, ... hoặc div.thuCN)
                    const dateByThu = {};
                    for (const div of Array.from(parsed.querySelectorAll('div[class^="thu"]'))) {
                        const cls = String(div.className || '');
                        const m = cls.match(/^thu([0-9A-Za-z]+)/);
                        if (m) dateByThu[normalizeThu(m[1])] = textOf(div);
                    }

                    const entries = [];
                    const seenIds = new Set();
                    const cells = Array.from(parsed.querySelectorAll('td[chitiet_id]'));
                    for (const td of cells) {
                        const chitietId = String(td.getAttribute('chitiet_id') || '').trim();
                        if (!chitietId) continue;            // ô trống -> bỏ qua
                        if (seenIds.has(chitietId)) continue; // tránh trùng id
                        seenIds.add(chitietId);

                        const thu = normalizeThu(td.getAttribute('thu'));
                        const buoi = normalizeBuoi(td.getAttribute('buoi'));
                        const tiet = String(td.getAttribute('tiet') || '').trim();
                        const ppct = String(td.getAttribute('tiet_ppct') || '').trim();
                        const noiDung = String(td.getAttribute('noi_dung') || '').trim();
                        const nhanXet = String(td.getAttribute('nhan_xet') || '').trim();

                        // Đọc môn học hiển thị + ký tên từ các cell cùng dòng (nếu có)
                        let monHoc = '';
                        let kyTen = '';
                        const tr = td.closest('tr');
                        if (tr) {
                            const rowCells = Array.from(tr.querySelectorAll('td'));
                            const tdIndex = rowCells.indexOf(td);
                            // Cột Môn học đứng ngay sau cột Tiết (action, tiet, mon_hoc, ppct...)
                            if (tdIndex >= 0 && rowCells[tdIndex + 2]) {
                                monHoc = textOf(rowCells[tdIndex + 2]);
                            }
                            // Cột Ký tên là cell cuối cùng
                            if (rowCells.length) {
                                kyTen = textOf(rowCells[rowCells.length - 1]);
                            }
                        }

                        entries.push({
                            chitiet_id: chitietId,
                            thu: thu,
                            thu_full: dateByThu[thu] || '',
                            buoi: buoi,
                            tiet: tiet,
                            mon_hoc: monHoc,
                            ppct: ppct,
                            noi_dung: noiDung,
                            nhan_xet: nhanXet,
                            ky_ten: kyTen,
                        });
                    }

                    let weekValue = null;
                    const weekHidden = parsed.querySelector('input[name="iTuanHoc"]');
                    if (weekHidden && weekHidden.getAttribute('value')) {
                        const weekNum = parseInt(weekHidden.getAttribute('value'), 10);
                        if (!Number.isNaN(weekNum)) weekValue = weekNum;
                    }
                    if (weekValue === null) {
                        const weekMatch = html.match(/TUẦN\\s+(\\d+)/i);
                        if (weekMatch) weekValue = parseInt(weekMatch[1], 10);
                    }

                    return {
                        ok: true,
                        entries: entries,
                        week: weekValue,
                        fetch_ms: fetchMs,
                        lop: String(getRecordValue(classRec, lopDisplayField) || args.lopText),
                        class_id: classId,
                    };
                }''',
                {"lopText": str(lop_text), "tuanNum": int(tuan_num), "timeoutMs": int(timeout_ms)},
            )
            if result.get("ok"):
                return True, result
            return False, result.get("error", "Fetch tiết cần xóa thất bại")
        except PlaywrightTimeout:
            return False, f"Timeout fetch tiết cần xóa cho lớp {lop_text}, tuần {tuan_num}"
        except Exception as e:
            return False, f"Lỗi fetch tiết cần xóa: {type(e).__name__}: {str(e)[:120]}"

    def delete_entry_by_id(self, chitiet_id, timeout_s=12.0):
        """Xóa 1 tiết sổ đầu bài qua API nội bộ VnEdu (theo chitiet_id).

        Dùng đúng endpoint mà nút Xóa đỏ gọi:
            POST ?call=app.sodaubai.serv.so_dau_bai.delete  data={id: chitiet_id}
        Bỏ qua popup confirm — dùng session/cookie hiện tại của Chrome.

        Args:
            chitiet_id: str|int — id bản ghi chi tiết sổ đầu bài

        Returns:
            (success: bool, message: str)
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        target_id = str(chitiet_id or "").strip()
        if not target_id:
            return False, "Thiếu chitiet_id để xóa"

        timeout_ms = max(int(timeout_s * 1000), 3000)
        try:
            result = self.page.evaluate(
                '''async (args) => {
                    if (!(window.$ && $.ajax)) {
                        return {ok: false, error: 'jQuery.ajax không sẵn sàng', request_sent: false};
                    }
                    const basePath = (typeof applicationPath !== 'undefined' && applicationPath)
                        ? String(applicationPath) : '';
                    const url = (basePath || '') + '?call=app.sodaubai.serv.so_dau_bai.delete';

                    let requestSent = false;
                    let ajaxResult = null;
                    try {
                        ajaxResult = await new Promise((resolve) => {
                            requestSent = true;
                            const timeoutMs = Math.max(parseInt(args.timeoutMs || 0, 10) || 0, 3000);
                            const timer = setTimeout(() => {
                                resolve({ok: false, error: 'Timeout xóa sau ' + timeoutMs + 'ms', timeout: true});
                            }, timeoutMs);
                            $.ajax({
                                url: url,
                                type: 'post',
                                data: {id: args.id},
                                dataType: 'json',
                                success: function (rs) {
                                    clearTimeout(timer);
                                    resolve({ok: true, raw: rs});
                                },
                                error: function (xhr, status, thrown) {
                                    clearTimeout(timer);
                                    resolve({
                                        ok: false,
                                        error: String(thrown || (xhr && xhr.statusText) || 'ajax_error'),
                                        status: xhr && typeof xhr.status !== 'undefined' ? xhr.status : null,
                                        responseText: xhr && xhr.responseText ? String(xhr.responseText).slice(0, 200) : '',
                                    });
                                }
                            });
                        });
                    } catch (e) {
                        return {ok: false, error: String(e && e.message ? e.message : e), request_sent: requestSent};
                    }

                    if (!ajaxResult || !ajaxResult.ok) {
                        return {
                            ok: false,
                            error: ajaxResult && ajaxResult.error ? ajaxResult.error : 'ajax_error',
                            status_code: ajaxResult && ajaxResult.status,
                            response_text: ajaxResult && ajaxResult.responseText,
                            request_sent: requestSent,
                            timeout: !!(ajaxResult && ajaxResult.timeout),
                        };
                    }

                    let data = ajaxResult.raw;
                    if (typeof data === 'string') {
                        try { data = JSON.parse(data); }
                        catch (e) {
                            return {ok: false, error: 'JSON parse lỗi', raw: String(ajaxResult.raw).slice(0, 200), request_sent: requestSent};
                        }
                    }

                    // VnEdu trả success===false khi lỗi; thiếu field success coi như OK
                    if (data && data.success === false) {
                        return {ok: false, error: data.msg ? String(data.msg) : 'Server báo xóa thất bại', request_sent: requestSent};
                    }

                    let refreshClicked = false;
                    try {
                        const refreshBtn = document.querySelector('#sodaubai_refresh');
                        if (refreshBtn) { refreshBtn.click(); refreshClicked = true; }
                    } catch (e) {}

                    return {ok: true, server_msg: data && data.msg ? String(data.msg) : '', refresh_clicked: refreshClicked, request_sent: requestSent};
                }''',
                {"id": target_id, "timeoutMs": int(timeout_ms)},
            )
            if result.get("ok"):
                return True, result.get("server_msg", "") or "Đã xóa"
            return False, result.get("error", "Xóa thất bại")
        except PlaywrightTimeout:
            return False, f"Timeout xóa chitiet_id={target_id}"
        except Exception as e:
            return False, f"Lỗi xóa: {type(e).__name__}: {str(e)[:120]}"

    def get_open_lesson_form_snapshot(self):
        """Đọc nhanh dữ liệu hiện có trong popup tiết học đang mở."""
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        try:
            result = self.page.evaluate(
                '''() => {
                    function isLessonTitle(title) {
                        const t = String(title || '').toLowerCase();
                        return (
                            t.indexOf('chi ti') >= 0 ||
                            t.indexOf('ti\\u1EBFt h\\u1ECDc') >= 0 ||
                            t.indexOf('s\\u1ED5 \\u0111\\u1EA7u b\\u00E0i') >= 0 ||
                            t.indexOf('so dau bai') >= 0
                        );
                    }

                    if (!(window.Ext && Ext.ComponentQuery)) {
                        return {ok: false, error: 'ExtJS not available'};
                    }

                    let formPanel = null;
                    let title = '';
                    const wins = Ext.ComponentQuery.query('window');
                    for (const win of wins) {
                        try {
                            if (!(win.isVisible && win.isVisible())) continue;
                            if (!isLessonTitle(win.title || '')) continue;
                            const candidate = win.down ? win.down('form') : null;
                            if (candidate && candidate.getForm) {
                                formPanel = candidate;
                                title = String(win.title || '');
                                break;
                            }
                        } catch (e) {}
                    }

                    if (!formPanel || !formPanel.getForm) {
                        return {ok: false, error: 'Không tìm thấy popup chi tiết tiết học'};
                    }

                    const form = formPanel.getForm();
                    const fields = form.getFields().items || [];
                    const payload = {title: title, fields: {}, labels: {}};
                    for (const field of fields) {
                        let name = '';
                        if (!field) continue;
                        try { name = field.getName ? field.getName() : (field.name || ''); } catch (e) {}
                        if (!name) continue;
                        let value = '';
                        let raw = '';
                        let label = '';
                        let xtype = '';
                        try { value = field.getValue ? field.getValue() : ''; } catch (e) {}
                        try { raw = field.getRawValue ? field.getRawValue() : ''; } catch (e) {}
                        try { label = field.fieldLabel || ''; } catch (e) {}
                        try { xtype = field.getXType ? field.getXType() : (field.xtype || ''); } catch (e) {}
                        payload.fields[name] = {
                            value: String(value == null ? '' : value),
                            raw: String(raw == null ? '' : raw),
                            label: String(label || ''),
                            xtype: String(xtype || ''),
                        };
                    }
                    return {ok: true, payload: payload};
                }'''
            )
            if result.get("ok"):
                return True, result.get("payload", {})
            return False, result.get("error", "Không đọc được popup tiết học")
        except Exception as e:
            return False, f"Lỗi đọc popup tiết học: {type(e).__name__}: {str(e)[:120]}"

    def fill_form_minimal(self, hs_nghi, nhan_xet, diem):
        """Chỉ điền các field an toàn cho mode KHDH: nghỉ, nhận xét, điểm."""
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        payload = {
            "hsNghi": str(hs_nghi),
            "nhanXet": str(nhan_xet),
            "diem": str(diem),
        }
        try:
            result = self.page.evaluate(
                '''(args) => {
                    function isLessonTitle(title) {
                        const t = String(title || '').toLowerCase();
                        return (
                            t.indexOf('chi ti') >= 0 ||
                            t.indexOf('ti\\u1EBFt h\\u1ECDc') >= 0 ||
                            t.indexOf('s\\u1ED5 \\u0111\\u1EA7u b\\u00E0i') >= 0 ||
                            t.indexOf('so dau bai') >= 0
                        );
                    }

                    function setPlainField(form, fieldName, value, label, log, errors) {
                        if (value === null || value === undefined || value === '') {
                            log.push(label + ': skipped');
                            return true;
                        }
                        try {
                            const field = form.findField(fieldName);
                            if (!field) {
                                errors.push('Field not found: ' + fieldName);
                                return false;
                            }
                            field.setValue(value);
                            try {
                                field.fireEvent('change', field, value, field.originalValue);
                                field.fireEvent('blur', field);
                            } catch (e2) {}
                            log.push(label + '=' + String(value).substring(0, 40));
                            return true;
                        } catch (e3) {
                            errors.push(label + ': ' + e3.message);
                            return false;
                        }
                    }

                    if (!(window.Ext && Ext.ComponentQuery)) {
                        return {ok: false, errors: ['ExtJS not available'], log: []};
                    }

                    let formPanel = null;
                    const wins = Ext.ComponentQuery.query('window');
                    for (const win of wins) {
                        try {
                            if (!(win.isVisible && win.isVisible())) continue;
                            if (!isLessonTitle(win.title || '')) continue;
                            const candidate = win.down ? win.down('form') : null;
                            if (candidate && candidate.getForm) {
                                formPanel = candidate;
                                break;
                            }
                        } catch (e) {}
                    }
                    if (!formPanel || !formPanel.getForm) {
                        return {ok: false, errors: ['Không tìm thấy popup chi tiết tiết học'], log: []};
                    }

                    const form = formPanel.getForm();
                    const log = [];
                    const errors = [];
                    setPlainField(form, 'soluong_nghi', args.hsNghi, 'soluong_nghi', log, errors);
                    setPlainField(form, 'nhan_xet', args.nhanXet, 'nhan_xet', log, errors);
                    setPlainField(form, 'diem', args.diem, 'diem', log, errors);
                    return {ok: errors.length === 0, errors: errors, log: log};
                }''',
                payload,
            )

            if result.get("ok"):
                return True, "Đã nhập tối thiểu: " + ", ".join(result.get("log", []))
            errors = result.get("errors", [])
            return False, "Lỗi nhập tối thiểu: " + "; ".join(errors)
        except Exception as e:
            return False, f"Lỗi nhập tối thiểu: {type(e).__name__}: {str(e)[:120]}"

    @staticmethod
    def _normalize_thu_token(value):
        """Quy mọi biểu diễn 'thứ' về một token chuẩn: '2'..'7' hoặc 'CN'.

        Xử lý nhất quán mọi nguồn dữ liệu khác nhau:
          - UI slot: 8 / '8'        → 'CN'
          - read_table: 'CN\\n14/03' → 'CN'  | '2\\n09/03' → '2'
          - fetch_sodaubai_rows: 'CN' → 'CN'
          - read_khdh_suggested_rows: '8' → 'CN'

        Lưu ý: phải kiểm tra CN TRƯỚC khi regex [2-7] để tránh bắt nhầm
        chữ số trong phần ngày tháng (vd 'CN\\n14/03/2026' chứa '4','3','2').
        """
        text = str(value if value is not None else "").strip()
        if not text:
            return ""
        upper = text.upper()
        if upper.startswith("CN") or text == "8":
            return "CN"
        match = re.search(r"[2-7]", text)
        if match:
            return match.group()
        if "8" in text:
            return "CN"
        return upper

    @staticmethod
    def _normalize_buoi_token(value):
        """Quy 'buổi' về token chuẩn 'sang'/'chieu' (bỏ dấu, không phân biệt hoa thường)."""
        text = unicodedata.normalize("NFD", str(value if value is not None else ""))
        text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
        text = text.strip().lower()
        if "sang" in text or text == "1":
            return "sang"
        if "chieu" in text or text == "2":
            return "chieu"
        return text

    @staticmethod
    def _slot_row_matches(row, thu, buoi, tiet):
        """So khớp một row của bảng với slot mục tiêu.

        Dùng token chuẩn hoá cho thu/buoi để khớp được Chủ nhật (CN/8) và
        các biến thể dấu/hoa-thường giữa các nguồn dữ liệu khác nhau.
        """
        row_thu = ChromeBridge._normalize_thu_token(row.get("thu", ""))
        row_buoi = ChromeBridge._normalize_buoi_token(row.get("buoi", ""))
        row_tiet = str(row.get("tiet", "")).strip()
        return (
            row_thu == ChromeBridge._normalize_thu_token(thu)
            and row_buoi == ChromeBridge._normalize_buoi_token(buoi)
            and row_tiet == str(tiet).strip()
        )

    def wait_for_slot_data(self, thu, buoi, tiet, timeout_s=6.0, poll_interval=0.5):
        """Chờ đến khi 1 slot trên bảng xuất hiện dữ liệu sau khi lưu.

        Args:
            thu: int|str — thứ trong tuần
            buoi: str — "Sáng" | "Chiều"
            tiet: int|str — tiết học
            timeout_s: float — thời gian chờ tối đa
            poll_interval: float — chu kỳ poll

        Returns:
            (success: bool, message: str, row: dict|None)
        """
        deadline = time.time() + max(timeout_s, 0.5)
        target_thu = str(thu).strip()
        target_buoi = str(buoi).strip()
        target_tiet = str(tiet).strip()
        last_msg = "Không tìm thấy row mục tiêu"
        last_row = None

        while time.time() < deadline:
            ok, table_data = self.read_table()
            if not ok:
                last_msg = f"Lỗi đọc bảng: {table_data}"
                time.sleep(poll_interval)
                continue

            for row in table_data:
                if self._slot_row_matches(row, target_thu, target_buoi, target_tiet):
                    last_row = row
                    if row.get("has_data", False):
                        mon_hoc = str(row.get("mon_hoc", "")).strip()
                        return True, f"Row đã có dữ liệu ({mon_hoc or 'không rõ môn'})", row
                    last_msg = "Row đã tìm thấy nhưng chưa có dữ liệu"
                    break

            time.sleep(poll_interval)

        return False, last_msg, last_row

    def fetch_sodaubai_rows(self, lop_text, tuan_num, timeout_s=12.0, class_meta=None,
                            show_goi_y=False):
        """Fetch trực tiếp HTML sổ đầu bài theo lớp/tuần rồi parse ra rows.

        Đi đường service nội bộ của VnEdu để tránh tình trạng UI dropdown đã đổi
        nhưng grid chưa refresh kịp.

        Returns:
            (success, payload|message)
            payload = {
                "rows": list[dict],
                "week": int|None,
                "fetch_ms": int,
                "html_length": int,
                "lop": str,
            }
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        timeout_ms = max(int(timeout_s * 1000), 3000)
        try:
            result = self.page.evaluate(
                '''async (args) => {
                    function normalize(value) {
                        try {
                            return String(value == null ? '' : value)
                                .normalize('NFD')
                                .replace(/[\\u0300-\\u036f]/g, '')
                                .replace(/\\s+/g, ' ')
                                .trim()
                                .toLowerCase();
                        } catch (e) {
                            return String(value == null ? '' : value)
                                .replace(/\\s+/g, ' ')
                                .trim()
                                .toLowerCase();
                        }
                    }

                    function getCombo(name) {
                        if (typeof Ext === 'undefined' || !Ext.ComponentQuery) return null;
                        const combos = Ext.ComponentQuery.query('combobox');
                        for (const combo of combos) {
                            try {
                                const comboName = combo.getName ? combo.getName() : (combo.name || '');
                                if (comboName === name) return combo;
                            } catch (e) {}
                        }
                        return null;
                    }

                    function getStoreItems(store) {
                        if (!store) return [];
                        if (store.data && Array.isArray(store.data.items)) {
                            return store.data.items;
                        }
                        return [];
                    }

                    function getRecordValue(record, fieldName) {
                        if (!record) return '';
                        try {
                            if (record.get && typeof record.get === 'function') {
                                return record.get(fieldName);
                            }
                        } catch (e) {}
                        try {
                            return record.data ? record.data[fieldName] : '';
                        } catch (e2) {
                            return '';
                        }
                    }

                    function textOf(node) {
                        return String(node ? (node.textContent || '') : '')
                            .replace(/\\u00a0/g, ' ')
                            .replace(/\\s+/g, ' ')
                            .trim();
                    }

                    function parseRowsFromTable(rootDoc) {
                        let mainTable = rootDoc.querySelector('table.table');
                        if (!mainTable) {
                            const tables = rootDoc.querySelectorAll('table');
                            let maxRowspan = 0;
                            for (const t of tables) {
                                const rowspans = t.querySelectorAll('td[rowspan]');
                                if (rowspans.length > maxRowspan) {
                                    maxRowspan = rowspans.length;
                                    mainTable = t;
                                }
                            }
                        }
                        if (!mainTable) {
                            return {ok: false, error: 'Không tìm thấy bảng dữ liệu trong HTML fetch'};
                        }

                        const addSelector = 'a.add_tiet_so_dau_bai, a[onclick*="themChiTietSoDauBai"]';
                        const allRows = mainTable.querySelectorAll('tr');
                        const dataRows = [];

                        function isHeaderMarkerRow(row) {
                            const cells = Array.from(row.querySelectorAll('td'));
                            if (cells.length < 8) return false;
                            const texts = cells.map((cell) => textOf(cell));
                            const prefix = ['1', '2', '3', '4', '5'];
                            return prefix.every((value, index) => texts[index] === value);
                        }

                        function findDataStartIndex(rows) {
                            for (let i = 0; i < rows.length; i++) {
                                if (isHeaderMarkerRow(rows[i])) {
                                    return i + 1;
                                }
                            }

                            for (let i = 0; i < rows.length; i++) {
                                const cells = rows[i].querySelectorAll('td');
                                if (cells.length < 4) continue;
                                const firstText = textOf(cells[0]);
                                const secondText = textOf(cells[1]);
                                const firstRs = parseInt(cells[0].getAttribute('rowspan') || '0', 10);
                                const secondRs = parseInt(cells[1].getAttribute('rowspan') || '0', 10);
                                const looksLikeThu = (
                                    firstRs >= 5 &&
                                    (
                                        /^(CN|[2-7])(\\s|$|\\n|\\/|-|\\d)/i.test(firstText) ||
                                        /\\d{2}\\/\\d{2}\\/\\d{4}/.test(firstText)
                                    )
                                );
                                const looksLikeBuoi = (
                                    normalize(secondText).indexOf('sang') >= 0 ||
                                    normalize(secondText).indexOf('chieu') >= 0 ||
                                    secondRs >= 4
                                );
                                if (looksLikeThu || looksLikeBuoi) {
                                    return i;
                                }
                            }

                            for (let i = 0; i < rows.length; i++) {
                                if (rows[i].querySelector(addSelector)) {
                                    return i;
                                }
                            }

                            return 0;
                        }

                        const dataStartIdx = findDataStartIndex(allRows);

                        let currentThu = '';
                        let currentThuFull = '';
                        let currentBuoi = '';
                        let thuRemaining = 0;
                        let buoiRemaining = 0;
                        let addBtnCounter = 0;

                        for (let i = dataStartIdx; i < allRows.length; i++) {
                            const cells = allRows[i].querySelectorAll('td');
                            if (cells.length < 2) continue;

                            let cellIdx = 0;
                            let thu = currentThu;
                            let thuFull = currentThuFull;
                            let buoi = currentBuoi;
                            let tiet = '';
                            let monHoc = '';
                            let ppct = '';
                            let hasData = false;
                            let hasAddBtn = false;
                            let actionCell = null;
                            let chiTietId = '';
                            let monHocId = '';
                            let phanMonId = '';
                            let tietPpctAttr = '';
                            let giaoVienIdDayCung = '';
                            let thongBao = '';
                            let redTexts = [];

                            if (thuRemaining <= 0 && cells[cellIdx]) {
                                const rs = parseInt(cells[cellIdx].getAttribute('rowspan') || '0', 10);
                                if (rs > 1) {
                                    const rawThuText = String(cells[cellIdx].textContent || '')
                                        .replace(/\\u00a0/g, ' ')
                                        .trim();
                                    thuFull = textOf(cells[cellIdx]);
                                    const upperRaw = rawThuText.toUpperCase();
                                    if (upperRaw.startsWith('CN')) {
                                        thu = 'CN';
                                        const tail = rawThuText.slice(2).trim();
                                        if (tail) thuFull = 'CN\\n' + tail;
                                    } else {
                                        const match = rawThuText.match(/[2-7]/);
                                        thu = match ? match[0] : thuFull;
                                        if (match && rawThuText.startsWith(match[0])) {
                                            const tail = rawThuText.slice(match[0].length).trim();
                                            if (tail) thuFull = match[0] + '\\n' + tail;
                                        }
                                    }
                                    currentThu = thu;
                                    currentThuFull = thuFull;
                                    thuRemaining = rs;
                                    cellIdx++;
                                }
                            }

                            if (buoiRemaining <= 0 && cells[cellIdx]) {
                                const rs = parseInt(cells[cellIdx].getAttribute('rowspan') || '0', 10);
                                const text = textOf(cells[cellIdx]);
                                const lower = normalize(text);
                                const isBuoi = (
                                    lower.indexOf('sang') >= 0 ||
                                    lower.indexOf('chieu') >= 0 ||
                                    rs >= 4
                                );
                                if (isBuoi || rs > 1) {
                                    buoi = text;
                                    currentBuoi = buoi;
                                    buoiRemaining = rs > 0 ? rs : 5;
                                    cellIdx++;
                                }
                            }

                            if (cells[cellIdx]) {
                                actionCell = cells[cellIdx];
                                const btn = actionCell.querySelector(addSelector);
                                hasAddBtn = !!btn;
                                chiTietId = String(actionCell.getAttribute('chitiet_id') || '').trim();
                                monHocId = String(actionCell.getAttribute('mon_hoc_id') || '').trim();
                                phanMonId = String(actionCell.getAttribute('phan_mon_id') || '').trim();
                                tietPpctAttr = String(actionCell.getAttribute('tiet_ppct') || '').trim();
                                giaoVienIdDayCung = String(
                                    actionCell.getAttribute('giaovien_id_daycung') || ''
                                ).trim();
                                thongBao = String(actionCell.getAttribute('thong_bao') || '').trim();
                                cellIdx++;
                            }

                            if (cells[cellIdx]) {
                                tiet = textOf(cells[cellIdx]);
                                cellIdx++;
                            }

                            if (cells[cellIdx]) {
                                monHoc = textOf(cells[cellIdx]);
                                cellIdx++;
                            }

                            if (cells[cellIdx]) {
                                ppct = textOf(cells[cellIdx]);
                                cellIdx++;
                            }

                            let tenHsNghiTiet = '';
                            let noiDungCongViec = '';
                            let diemKiemTra = '';
                            let nhanXetGiaoVien = '';
                            let diemXepLoai = '';
                            let kyTen = '';

                            if (cells[cellIdx]) {
                                tenHsNghiTiet = textOf(cells[cellIdx]);
                                cellIdx++;
                            }

                            if (cells[cellIdx]) {
                                noiDungCongViec = textOf(cells[cellIdx]);
                                cellIdx++;
                            }

                            if (cells[cellIdx]) {
                                diemKiemTra = textOf(cells[cellIdx]);
                                cellIdx++;
                            }

                            if (cells[cellIdx]) {
                                nhanXetGiaoVien = textOf(cells[cellIdx]);
                                cellIdx++;
                            }

                            if (cells[cellIdx]) {
                                diemXepLoai = textOf(cells[cellIdx]);
                                cellIdx++;
                            }

                            if (cells[cellIdx]) {
                                kyTen = textOf(cells[cellIdx]);
                                cellIdx++;
                            }

                            redTexts = Array.from(allRows[i].querySelectorAll('span'))
                                .filter((span) => {
                                    const style = String(span.getAttribute('style') || '').toLowerCase();
                                    let computed = '';
                                    try {
                                        computed = String(window.getComputedStyle(span).color || '').toLowerCase();
                                    } catch (e) {}
                                    return style.includes('red')
                                        || style.includes('#f00')
                                        || style.includes('255, 0, 0')
                                        || computed.includes('red')
                                        || computed.includes('255, 0, 0');
                                })
                                .map((span) => textOf(span))
                                .filter(Boolean);
                            const hasMeaningfulValue = (value) => {
                                const normalized = normalize(value);
                                return Boolean(normalized)
                                    && !['0', 'false', 'null', 'undefined', 'none'].includes(normalized);
                            };
                            const hasSuggestionMetadata = Boolean(
                                hasMeaningfulValue(monHocId)
                                || hasMeaningfulValue(phanMonId)
                                || hasMeaningfulValue(tietPpctAttr)
                                || hasMeaningfulValue(giaoVienIdDayCung)
                                || redTexts.length
                            );
                            hasData = Boolean(chiTietId)
                                || (monHoc.length > 0 && !hasAddBtn && !hasSuggestionMetadata);
                            const isScheduled = !hasData && Boolean(
                                hasSuggestionMetadata || monHoc || ppct || hasMeaningfulValue(thongBao)
                            );
                            const isUnplanned = !hasData && !isScheduled;
                            thuRemaining--;
                            buoiRemaining--;

                            if (tiet && /^\\d+$/.test(tiet)) {
                                const addBtnIndex = hasAddBtn ? addBtnCounter : -1;
                                if (hasAddBtn) addBtnCounter++;
                                dataRows.push({
                                    index: dataRows.length,
                                    rowIdx: i,
                                    thu: thu,
                                    thu_full: thuFull,
                                    buoi: buoi,
                                    tiet: tiet,
                                    mon_hoc: monHoc,
                                    ppct: ppct,
                                    ten_hs_nghi_tiet: tenHsNghiTiet,
                                    noi_dung_cong_viec: noiDungCongViec,
                                    diem_kiem_tra: diemKiemTra,
                                    nhan_xet_giao_vien: nhanXetGiaoVien,
                                    diem_xep_loai: diemXepLoai,
                                    ky_ten: kyTen,
                                    has_data: hasData,
                                    is_scheduled: isScheduled,
                                    is_unplanned: isUnplanned,
                                    has_add_btn: hasAddBtn,
                                    add_btn_index: addBtnIndex,
                                    chitiet_id: chiTietId,
                                    mon_hoc_id: monHocId,
                                    phan_mon_id: phanMonId,
                                    tiet_ppct_attr: tietPpctAttr,
                                    giaovien_id_daycung: giaoVienIdDayCung,
                                    thong_bao: thongBao,
                                    ppct_hint: tietPpctAttr || ppct,
                                    mon_hoc_hint: monHoc,
                                    noi_dung_hint: noiDungCongViec,
                                    red_texts: redTexts,
                                });
                            }
                        }

                        return {ok: true, rows: dataRows};
                    }

                    if (typeof Ext === 'undefined' || !Ext.ComponentQuery) {
                        return {ok: false, error: 'ExtJS not available'};
                    }

                    const capCombo = getCombo('cboCapHoc');
                    const classMeta = args.classMeta || {};
                    const classMetaText = String(classMeta.text || '').trim();
                    const classMetaValue = String(classMeta.value || '').trim();
                    const classMetaKhoi = String(classMeta.khoi || '').trim();
                    const classMetaCap = String(classMeta.cap || '').trim();
                    let classDisplayText = classMetaText;
                    let classId = classMetaValue;
                    let khoiHoc = classMetaKhoi;
                    let capHoc = classMetaCap;

                    if (!classId) {
                        const lopCombo = getCombo('cboLopHoc');
                        if (!lopCombo || !lopCombo.store) {
                            return {ok: false, error: 'Không tìm thấy combobox lớp hoặc store lớp'};
                        }

                        const lopDisplayField = lopCombo.displayField || 'ten';
                        const lopValueField = lopCombo.valueField || 'id';
                        const lopItems = getStoreItems(lopCombo.store);
                        const targetLop = normalize(args.lopText);
                        let classRec = null;
                        for (const rec of lopItems) {
                            const label = normalize(getRecordValue(rec, lopDisplayField));
                            if (label === targetLop) {
                                classRec = rec;
                                break;
                            }
                        }
                        if (!classRec) {
                            return {ok: false, error: 'Không tìm thấy lớp ' + args.lopText + ' trong store hiện tại'};
                        }
                        classDisplayText = String(getRecordValue(classRec, lopDisplayField) || args.lopText || '').trim();
                        classId = String(getRecordValue(classRec, lopValueField) || '').trim();
                        khoiHoc = String(getRecordValue(classRec, 'khoi') || '').trim();
                        capHoc = String(
                            getRecordValue(classRec, 'cap')
                            || getRecordValue(classRec, 'cap_hoc')
                            || ''
                        ).trim();
                    }

                    if (!classId) {
                        return {ok: false, error: 'Thiếu class_id để fetch sổ đầu bài'};
                    }

                    if (!classDisplayText) {
                        classDisplayText = String(args.lopText || '').trim();
                    }
                    if (!khoiHoc) {
                        const khoiMatch = classDisplayText.match(/^(\\d{1,2})/);
                        khoiHoc = khoiMatch ? String(khoiMatch[1]) : '';
                    }
                    if (!capHoc) {
                        capHoc = capCombo
                            ? (capCombo.getValue ? capCombo.getValue() : capCombo.value)
                            : '';
                    }

                    const token = typeof myToken !== 'undefined'
                        ? (typeof myToken === 'function' ? myToken() : myToken)
                        : '';
                    const userId = typeof myUserId !== 'undefined'
                        ? (typeof myUserId === 'function' ? myUserId() : myUserId)
                        : '';
                    const namHoc = String(
                        typeof phpviet_nam_hoc_v5 !== 'undefined' ? phpviet_nam_hoc_v5 : 2025
                    );
                    const params = new URLSearchParams();
                    params.set('my_token', String(token || ''));
                    params.set('my_user_id', String(userId || ''));
                    params.set('app_nam_hoc', namHoc);
                    params.set('capHoc', String(capHoc || ''));
                    params.set('lopHoc', String(classId || ''));
                    params.set('khoiHoc', String(khoiHoc || ''));
                    params.set('tuanHoc', String(args.tuanNum));
                    params.set('show_goi_y', args.showGoiY ? '1' : '0');

                    const url = './?load=app.sodaubai.serv.so_dau_bai'
                        + '&app_nam_hoc=' + encodeURIComponent(namHoc)
                        + '&my_token=' + encodeURIComponent(String(token || ''));

                    const t0 = performance.now();
                    const timeoutMs = Math.max(parseInt(args.timeoutMs || 0, 10) || 0, 3000);
                    const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
                    let timeoutHandle = null;
                    if (controller) {
                        timeoutHandle = setTimeout(() => {
                            try {
                                controller.abort();
                            } catch (e) {}
                        }, timeoutMs);
                    }

                    let resp = null;
                    let html = '';
                    try {
                        resp = await fetch(url, {
                            method: 'POST',
                            headers: {'content-type': 'application/x-www-form-urlencoded; charset=UTF-8'},
                            body: params.toString(),
                            credentials: 'same-origin',
                            signal: controller ? controller.signal : undefined,
                        });
                        html = await resp.text();
                    } catch (error) {
                        const fetchMs = Math.round(performance.now() - t0);
                        const errorName = String(error && error.name ? error.name : '');
                        const isTimeout = errorName === 'AbortError';
                        return {
                            ok: false,
                            error: isTimeout
                                ? ('Timeout fetch service sau ' + timeoutMs + 'ms')
                                : ('Lỗi fetch service: ' + String(error && error.message ? error.message : error)),
                            error_code: isTimeout ? 'timeout' : 'fetch_error',
                            fetch_ms: fetchMs,
                        };
                    } finally {
                        if (timeoutHandle) {
                            clearTimeout(timeoutHandle);
                        }
                    }
                    const fetchMs = Math.round(performance.now() - t0);

                    if (!resp.ok) {
                        return {
                            ok: false,
                            error: 'Fetch service thất bại: HTTP ' + resp.status,
                            error_code: 'http_error',
                            status: resp.status,
                            fetch_ms: fetchMs,
                        };
                    }

                    const parser = new DOMParser();
                    const parsed = parser.parseFromString(html, 'text/html');
                    const parseResult = parseRowsFromTable(parsed);
                    if (!parseResult.ok) {
                        return {
                            ok: false,
                            error: parseResult.error,
                            error_code: 'parse_error',
                            fetch_ms: fetchMs,
                            html_length: html.length,
                        };
                    }

                    let weekValue = null;
                    const weekHidden = parsed.querySelector('input[name="iTuanHoc"]');
                    if (weekHidden && weekHidden.getAttribute('value')) {
                        const weekNum = parseInt(weekHidden.getAttribute('value'), 10);
                        if (!Number.isNaN(weekNum)) weekValue = weekNum;
                    }
                    if (weekValue === null) {
                        const weekMatch = html.match(/TUẦN\\s+(\\d+)/i);
                        if (weekMatch) weekValue = parseInt(weekMatch[1], 10);
                    }

                    return {
                        ok: true,
                        rows: parseResult.rows,
                        week: weekValue,
                        fetch_ms: fetchMs,
                        html_length: html.length,
                        lop: classDisplayText,
                        class_id: String(classId || ''),
                        khoi_hoc: String(khoiHoc || ''),
                        show_goi_y: !!args.showGoiY,
                    };
                }''',
                {
                    "lopText": str(lop_text),
                    "tuanNum": int(tuan_num),
                    "timeoutMs": int(timeout_ms),
                    "classMeta": copy.deepcopy(class_meta) if class_meta else {},
                    "showGoiY": bool(show_goi_y),
                },
            )
            if result.get("ok"):
                return True, result
            return False, result.get("error", "Fetch sổ đầu bài thất bại")
        except PlaywrightTimeout:
            return False, f"Timeout fetch sổ đầu bài cho lớp {lop_text}, tuần {tuan_num}"
        except Exception as e:
            return False, f"Lỗi fetch sổ đầu bài: {type(e).__name__}: {str(e)[:120]}"

    def fetch_sodaubai_rows_bulk(self, lop_text, tuan_nums, timeout_s=24.0, concurrency=6,
                                 class_meta=None, show_goi_y=False):
        """Fetch nhiều tuần sổ đầu bài trong một lần evaluate để giảm roundtrip.

        Returns:
            (success, payload|message)
            payload = {
                "results": [
                    {
                        "requested_week": int,
                        "ok": bool,
                        "payload": dict,   # nếu ok
                        "error": str,      # nếu fail
                    }
                ],
                "concurrency": int,
                "requested_count": int,
            }
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        week_numbers = []
        seen = set()
        for item in list(tuan_nums or []):
            try:
                week_num = int(item)
            except Exception:
                continue
            if week_num < 1 or week_num in seen:
                continue
            seen.add(week_num)
            week_numbers.append(week_num)

        if not week_numbers:
            return True, {"results": [], "concurrency": 0, "requested_count": 0}

        try:
            result = self.page.evaluate(
                '''async (args) => {
                    function normalize(value) {
                        try {
                            return String(value == null ? '' : value)
                                .normalize('NFD')
                                .replace(/[\\u0300-\\u036f]/g, '')
                                .replace(/\\s+/g, ' ')
                                .trim()
                                .toLowerCase();
                        } catch (e) {
                            return String(value == null ? '' : value)
                                .replace(/\\s+/g, ' ')
                                .trim()
                                .toLowerCase();
                        }
                    }

                    function getCombo(name) {
                        if (typeof Ext === 'undefined' || !Ext.ComponentQuery) return null;
                        const combos = Ext.ComponentQuery.query('combobox');
                        for (const combo of combos) {
                            try {
                                const comboName = combo.getName ? combo.getName() : (combo.name || '');
                                if (comboName === name) return combo;
                            } catch (e) {}
                        }
                        return null;
                    }

                    function getStoreItems(store) {
                        if (!store) return [];
                        if (store.data && Array.isArray(store.data.items)) {
                            return store.data.items;
                        }
                        return [];
                    }

                    function getRecordValue(record, fieldName) {
                        if (!record) return '';
                        try {
                            if (record.get && typeof record.get === 'function') {
                                return record.get(fieldName);
                            }
                        } catch (e) {}
                        try {
                            return record.data ? record.data[fieldName] : '';
                        } catch (e2) {
                            return '';
                        }
                    }

                    function textOf(node) {
                        return String(node ? (node.textContent || '') : '')
                            .replace(/\\u00a0/g, ' ')
                            .replace(/\\s+/g, ' ')
                            .trim();
                    }

                    function parseRowsFromTable(rootDoc) {
                        let mainTable = rootDoc.querySelector('table.table');
                        if (!mainTable) {
                            const tables = rootDoc.querySelectorAll('table');
                            let maxRowspan = 0;
                            for (const t of tables) {
                                const rowspans = t.querySelectorAll('td[rowspan]');
                                if (rowspans.length > maxRowspan) {
                                    maxRowspan = rowspans.length;
                                    mainTable = t;
                                }
                            }
                        }
                        if (!mainTable) {
                            return {ok: false, error: 'Không tìm thấy bảng dữ liệu trong HTML fetch'};
                        }

                        const addSelector = 'a.add_tiet_so_dau_bai, a[onclick*="themChiTietSoDauBai"]';
                        const allRows = mainTable.querySelectorAll('tr');
                        const dataRows = [];

                        function isHeaderMarkerRow(row) {
                            const cells = Array.from(row.querySelectorAll('td'));
                            if (cells.length < 8) return false;
                            const texts = cells.map((cell) => textOf(cell));
                            const prefix = ['1', '2', '3', '4', '5'];
                            return prefix.every((value, index) => texts[index] === value);
                        }

                        function findDataStartIndex(rows) {
                            for (let i = 0; i < rows.length; i++) {
                                if (isHeaderMarkerRow(rows[i])) {
                                    return i + 1;
                                }
                            }

                            for (let i = 0; i < rows.length; i++) {
                                const cells = rows[i].querySelectorAll('td');
                                if (cells.length < 4) continue;
                                const firstText = textOf(cells[0]);
                                const secondText = textOf(cells[1]);
                                const firstRs = parseInt(cells[0].getAttribute('rowspan') || '0', 10);
                                const secondRs = parseInt(cells[1].getAttribute('rowspan') || '0', 10);
                                const looksLikeThu = (
                                    firstRs >= 5 &&
                                    (
                                        /^(CN|[2-7])(\\s|$|\\n|\\/|-|\\d)/i.test(firstText) ||
                                        /\\d{2}\\/\\d{2}\\/\\d{4}/.test(firstText)
                                    )
                                );
                                const looksLikeBuoi = (
                                    normalize(secondText).indexOf('sang') >= 0 ||
                                    normalize(secondText).indexOf('chieu') >= 0 ||
                                    secondRs >= 4
                                );
                                if (looksLikeThu || looksLikeBuoi) {
                                    return i;
                                }
                            }

                            for (let i = 0; i < rows.length; i++) {
                                if (rows[i].querySelector(addSelector)) {
                                    return i;
                                }
                            }

                            return 0;
                        }

                        const dataStartIdx = findDataStartIndex(allRows);

                        let currentThu = '';
                        let currentThuFull = '';
                        let currentBuoi = '';
                        let thuRemaining = 0;
                        let buoiRemaining = 0;
                        let addBtnCounter = 0;

                        for (let i = dataStartIdx; i < allRows.length; i++) {
                            const cells = allRows[i].querySelectorAll('td');
                            if (cells.length < 2) continue;

                            let cellIdx = 0;
                            let thu = currentThu;
                            let thuFull = currentThuFull;
                            let buoi = currentBuoi;
                            let tiet = '';
                            let monHoc = '';
                            let ppct = '';
                            let hasData = false;
                            let hasAddBtn = false;
                            let actionCell = null;
                            let chiTietId = '';
                            let monHocId = '';
                            let phanMonId = '';
                            let tietPpctAttr = '';
                            let giaoVienIdDayCung = '';
                            let thongBao = '';
                            let redTexts = [];

                            if (thuRemaining <= 0 && cells[cellIdx]) {
                                const rs = parseInt(cells[cellIdx].getAttribute('rowspan') || '0', 10);
                                if (rs > 1) {
                                    const rawThuText = String(cells[cellIdx].textContent || '')
                                        .replace(/\\u00a0/g, ' ')
                                        .trim();
                                    thuFull = textOf(cells[cellIdx]);
                                    const upperRaw = rawThuText.toUpperCase();
                                    if (upperRaw.startsWith('CN')) {
                                        thu = 'CN';
                                        const tail = rawThuText.slice(2).trim();
                                        if (tail) thuFull = 'CN\\n' + tail;
                                    } else {
                                        const match = rawThuText.match(/[2-7]/);
                                        thu = match ? match[0] : thuFull;
                                        if (match && rawThuText.startsWith(match[0])) {
                                            const tail = rawThuText.slice(match[0].length).trim();
                                            if (tail) thuFull = match[0] + '\\n' + tail;
                                        }
                                    }
                                    currentThu = thu;
                                    currentThuFull = thuFull;
                                    thuRemaining = rs;
                                    cellIdx++;
                                }
                            }

                            if (buoiRemaining <= 0 && cells[cellIdx]) {
                                const rs = parseInt(cells[cellIdx].getAttribute('rowspan') || '0', 10);
                                const text = textOf(cells[cellIdx]);
                                const lower = normalize(text);
                                const isBuoi = (
                                    lower.indexOf('sang') >= 0 ||
                                    lower.indexOf('chieu') >= 0 ||
                                    rs >= 4
                                );
                                if (isBuoi || rs > 1) {
                                    buoi = text;
                                    currentBuoi = buoi;
                                    buoiRemaining = rs > 0 ? rs : 5;
                                    cellIdx++;
                                }
                            }

                            if (cells[cellIdx]) {
                                actionCell = cells[cellIdx];
                                const btn = actionCell.querySelector(addSelector);
                                hasAddBtn = !!btn;
                                chiTietId = String(actionCell.getAttribute('chitiet_id') || '').trim();
                                monHocId = String(actionCell.getAttribute('mon_hoc_id') || '').trim();
                                phanMonId = String(actionCell.getAttribute('phan_mon_id') || '').trim();
                                tietPpctAttr = String(actionCell.getAttribute('tiet_ppct') || '').trim();
                                giaoVienIdDayCung = String(
                                    actionCell.getAttribute('giaovien_id_daycung') || ''
                                ).trim();
                                thongBao = String(actionCell.getAttribute('thong_bao') || '').trim();
                                cellIdx++;
                            }

                            if (cells[cellIdx]) {
                                tiet = textOf(cells[cellIdx]);
                                cellIdx++;
                            }

                            if (cells[cellIdx]) {
                                monHoc = textOf(cells[cellIdx]);
                                cellIdx++;
                            }

                            if (cells[cellIdx]) {
                                ppct = textOf(cells[cellIdx]);
                                cellIdx++;
                            }

                            let tenHsNghiTiet = '';
                            let noiDungCongViec = '';
                            let diemKiemTra = '';
                            let nhanXetGiaoVien = '';
                            let diemXepLoai = '';
                            let kyTen = '';

                            if (cells[cellIdx]) {
                                tenHsNghiTiet = textOf(cells[cellIdx]);
                                cellIdx++;
                            }
                            if (cells[cellIdx]) {
                                noiDungCongViec = textOf(cells[cellIdx]);
                                cellIdx++;
                            }
                            if (cells[cellIdx]) {
                                diemKiemTra = textOf(cells[cellIdx]);
                                cellIdx++;
                            }
                            if (cells[cellIdx]) {
                                nhanXetGiaoVien = textOf(cells[cellIdx]);
                                cellIdx++;
                            }
                            if (cells[cellIdx]) {
                                diemXepLoai = textOf(cells[cellIdx]);
                                cellIdx++;
                            }
                            if (cells[cellIdx]) {
                                kyTen = textOf(cells[cellIdx]);
                                cellIdx++;
                            }

                            redTexts = Array.from(allRows[i].querySelectorAll('span'))
                                .filter((span) => {
                                    const style = String(span.getAttribute('style') || '').toLowerCase();
                                    let computed = '';
                                    try {
                                        computed = String(window.getComputedStyle(span).color || '').toLowerCase();
                                    } catch (e) {}
                                    return style.includes('red')
                                        || style.includes('#f00')
                                        || style.includes('255, 0, 0')
                                        || computed.includes('red')
                                        || computed.includes('255, 0, 0');
                                })
                                .map((span) => textOf(span))
                                .filter(Boolean);
                            const hasMeaningfulValue = (value) => {
                                const normalized = normalize(value);
                                return Boolean(normalized)
                                    && !['0', 'false', 'null', 'undefined', 'none'].includes(normalized);
                            };
                            const hasSuggestionMetadata = Boolean(
                                hasMeaningfulValue(monHocId)
                                || hasMeaningfulValue(phanMonId)
                                || hasMeaningfulValue(tietPpctAttr)
                                || hasMeaningfulValue(giaoVienIdDayCung)
                                || redTexts.length
                            );
                            hasData = Boolean(chiTietId)
                                || (monHoc.length > 0 && !hasAddBtn && !hasSuggestionMetadata);
                            const isScheduled = !hasData && Boolean(
                                hasSuggestionMetadata || monHoc || ppct || hasMeaningfulValue(thongBao)
                            );
                            const isUnplanned = !hasData && !isScheduled;
                            thuRemaining--;
                            buoiRemaining--;

                            if (tiet && /^\\d+$/.test(tiet)) {
                                const addBtnIndex = hasAddBtn ? addBtnCounter : -1;
                                if (hasAddBtn) addBtnCounter++;
                                dataRows.push({
                                    index: dataRows.length,
                                    rowIdx: i,
                                    thu: thu,
                                    thu_full: thuFull,
                                    buoi: buoi,
                                    tiet: tiet,
                                    mon_hoc: monHoc,
                                    ppct: ppct,
                                    ten_hs_nghi_tiet: tenHsNghiTiet,
                                    noi_dung_cong_viec: noiDungCongViec,
                                    diem_kiem_tra: diemKiemTra,
                                    nhan_xet_giao_vien: nhanXetGiaoVien,
                                    diem_xep_loai: diemXepLoai,
                                    ky_ten: kyTen,
                                    has_data: hasData,
                                    is_scheduled: isScheduled,
                                    is_unplanned: isUnplanned,
                                    has_add_btn: hasAddBtn,
                                    add_btn_index: addBtnIndex,
                                    chitiet_id: chiTietId,
                                    mon_hoc_id: monHocId,
                                    phan_mon_id: phanMonId,
                                    tiet_ppct_attr: tietPpctAttr,
                                    giaovien_id_daycung: giaoVienIdDayCung,
                                    thong_bao: thongBao,
                                    ppct_hint: tietPpctAttr || ppct,
                                    mon_hoc_hint: monHoc,
                                    noi_dung_hint: noiDungCongViec,
                                    red_texts: redTexts,
                                });
                            }
                        }

                        return {ok: true, rows: dataRows};
                    }

                    if (typeof Ext === 'undefined' || !Ext.ComponentQuery) {
                        return {ok: false, error: 'ExtJS not available'};
                    }

                    const capCombo = getCombo('cboCapHoc');
                    const classMeta = args.classMeta || {};
                    const classMetaText = String(classMeta.text || '').trim();
                    const classMetaValue = String(classMeta.value || '').trim();
                    const classMetaKhoi = String(classMeta.khoi || '').trim();
                    const classMetaCap = String(classMeta.cap || '').trim();
                    let lopDisplay = classMetaText;
                    let classId = classMetaValue;
                    let khoiHoc = classMetaKhoi;
                    let capHoc = classMetaCap;

                    if (!classId) {
                        const lopCombo = getCombo('cboLopHoc');
                        if (!lopCombo || !lopCombo.store) {
                            return {ok: false, error: 'Không tìm thấy combobox lớp hoặc store lớp'};
                        }
                        const lopDisplayField = lopCombo.displayField || 'ten';
                        const lopValueField = lopCombo.valueField || 'id';
                        const lopItems = getStoreItems(lopCombo.store);
                        const targetLop = normalize(args.lopText);
                        let classRec = null;
                        for (const rec of lopItems) {
                            const label = normalize(getRecordValue(rec, lopDisplayField));
                            if (label === targetLop) {
                                classRec = rec;
                                break;
                            }
                        }
                        if (!classRec) {
                            return {ok: false, error: 'Không tìm thấy lớp ' + args.lopText + ' trong store hiện tại'};
                        }
                        lopDisplay = String(getRecordValue(classRec, lopDisplayField) || args.lopText);
                        classId = String(getRecordValue(classRec, lopValueField) || '').trim();
                        khoiHoc = String(getRecordValue(classRec, 'khoi') || '').trim();
                        capHoc = String(
                            getRecordValue(classRec, 'cap')
                            || getRecordValue(classRec, 'cap_hoc')
                            || ''
                        ).trim();
                    }
                    if (!classId) {
                        return {ok: false, error: 'Thiếu class_id để bulk fetch sổ đầu bài'};
                    }
                    if (!lopDisplay) lopDisplay = String(args.lopText || '').trim();
                    if (!khoiHoc) {
                        const khoiMatch = lopDisplay.match(/^(\\d{1,2})/);
                        khoiHoc = khoiMatch ? String(khoiMatch[1]) : '';
                    }
                    if (!capHoc) {
                        capHoc = capCombo
                            ? (capCombo.getValue ? capCombo.getValue() : capCombo.value)
                            : 2;
                    }

                    const token = typeof myToken !== 'undefined'
                        ? (typeof myToken === 'function' ? myToken() : myToken)
                        : '';
                    const userId = typeof myUserId !== 'undefined'
                        ? (typeof myUserId === 'function' ? myUserId() : myUserId)
                        : '';
                    const namHoc = String(
                        typeof phpviet_nam_hoc_v5 !== 'undefined' ? phpviet_nam_hoc_v5 : 2025
                    );
                    const url = './?load=app.sodaubai.serv.so_dau_bai'
                        + '&app_nam_hoc=' + encodeURIComponent(namHoc)
                        + '&my_token=' + encodeURIComponent(String(token || ''));

                    async function fetchOne(weekNum) {
                        try {
                            const params = new URLSearchParams();
                            params.set('my_token', String(token || ''));
                            params.set('my_user_id', String(userId || ''));
                            params.set('app_nam_hoc', namHoc);
                            params.set('capHoc', String(capHoc || ''));
                            params.set('lopHoc', classId);
                            params.set('khoiHoc', String(khoiHoc || ''));
                            params.set('tuanHoc', String(weekNum));
                            params.set('show_goi_y', args.showGoiY ? '1' : '0');

                            const controller = new AbortController();
                            const timeoutId = setTimeout(() => controller.abort(), Math.max(3000, args.timeoutMs || 12000));
                            const t0 = performance.now();
                            let resp;
                            let html = '';
                            try {
                                resp = await fetch(url, {
                                    method: 'POST',
                                    headers: {'content-type': 'application/x-www-form-urlencoded; charset=UTF-8'},
                                    body: params.toString(),
                                    credentials: 'same-origin',
                                    signal: controller.signal,
                                });
                                html = await resp.text();
                            } finally {
                                clearTimeout(timeoutId);
                            }
                            const fetchMs = Math.round(performance.now() - t0);

                            if (!resp.ok) {
                                return {
                                    requested_week: weekNum,
                                    ok: false,
                                    error: 'Fetch service thất bại: HTTP ' + resp.status,
                                };
                            }

                            const parser = new DOMParser();
                            const parsed = parser.parseFromString(html, 'text/html');
                            const parseResult = parseRowsFromTable(parsed);
                            if (!parseResult.ok) {
                                return {
                                    requested_week: weekNum,
                                    ok: false,
                                    error: parseResult.error,
                                };
                            }

                            let weekValue = null;
                            const weekHidden = parsed.querySelector('input[name="iTuanHoc"]');
                            if (weekHidden && weekHidden.getAttribute('value')) {
                                const weekNumHidden = parseInt(weekHidden.getAttribute('value'), 10);
                                if (!Number.isNaN(weekNumHidden)) weekValue = weekNumHidden;
                            }
                            if (weekValue === null) {
                                const weekMatch = html.match(/TUẦN\\s+(\\d+)/i);
                                if (weekMatch) weekValue = parseInt(weekMatch[1], 10);
                            }
                            if (weekValue !== null && parseInt(weekValue, 10) !== parseInt(weekNum, 10)) {
                                return {
                                    requested_week: weekNum,
                                    ok: false,
                                    error: 'Service trả về tuần ' + weekValue + ', không khớp tuần yêu cầu ' + weekNum,
                                };
                            }

                            return {
                                requested_week: weekNum,
                                ok: true,
                                payload: {
                                    rows: parseResult.rows,
                                    week: weekValue,
                                    fetch_ms: fetchMs,
                                    html_length: html.length,
                                    lop: lopDisplay,
                                    class_id: classId,
                                    khoi_hoc: String(khoiHoc || ''),
                                    show_goi_y: !!args.showGoiY,
                                },
                            };
                        } catch (e) {
                            return {
                                requested_week: weekNum,
                                ok: false,
                                error: 'Lỗi fetch tuần ' + weekNum + ': ' + String(e && e.message ? e.message : e),
                            };
                        }
                    }

                    const queue = Array.from(new Set((args.tuanNums || []).map((x) => parseInt(x, 10)).filter((x) => !Number.isNaN(x) && x > 0)));
                    const requestedCount = queue.length;
                    const results = [];
                    const workerCount = Math.max(1, Math.min(parseInt(args.concurrency || 6, 10) || 6, queue.length));

                    async function worker() {
                        while (queue.length) {
                            const weekNum = queue.shift();
                            if (!weekNum) break;
                            const item = await fetchOne(weekNum);
                            results.push(item);
                        }
                    }

                    await Promise.all(Array.from({length: workerCount}, () => worker()));
                    results.sort((a, b) => {
                        return parseInt(a.requested_week || 0, 10) - parseInt(b.requested_week || 0, 10);
                    });

                    return {
                        ok: true,
                        results: results,
                        concurrency: workerCount,
                        requested_count: requestedCount,
                    };
                }''',
                {
                    "lopText": str(lop_text),
                    "tuanNums": week_numbers,
                    "timeoutMs": max(int(timeout_s * 1000), 3000),
                    "concurrency": max(1, min(int(concurrency or 6), 8)),
                    "classMeta": copy.deepcopy(class_meta) if class_meta else {},
                    "showGoiY": bool(show_goi_y),
                },
            )
            if result.get("ok"):
                return True, result
            return False, result.get("error", "Bulk fetch sổ đầu bài thất bại")
        except PlaywrightTimeout:
            return False, f"Timeout bulk fetch sổ đầu bài cho lớp {lop_text}"
        except Exception as e:
            return False, f"Lỗi bulk fetch sổ đầu bài: {type(e).__name__}: {str(e)[:120]}"

    def wait_for_slot_data_fetch(self, lop_text, tuan_num, thu, buoi, tiet,
                                 timeout_s=5.0, poll_interval=0.35):
        """Xác minh slot đã commit bằng cách refetch HTML sổ đầu bài từ service."""
        deadline = time.time() + max(timeout_s, 0.5)
        last_msg = "Không tìm thấy row mục tiêu trong HTML fetch"
        last_row = None
        target_thu = str(thu).strip()
        target_buoi = str(buoi).strip()
        target_tiet = str(tiet).strip()
        attempt = 0
        sleep_steps = [0.18, 0.25, 0.35, 0.45, 0.6]

        while time.time() < deadline:
            fetch_timeout = max(min(poll_interval * 4, 4.5), 2.5)
            ok, payload = self.fetch_sodaubai_rows(
                lop_text,
                tuan_num,
                timeout_s=fetch_timeout,
            )
            if not ok:
                last_msg = str(payload)
                sleep_s = min(
                    sleep_steps[min(attempt, len(sleep_steps) - 1)],
                    max(deadline - time.time(), 0),
                )
                attempt += 1
                if sleep_s > 0:
                    time.sleep(sleep_s)
                continue

            server_week = payload.get("week")
            if server_week is not None and int(server_week) != int(tuan_num):
                last_msg = (
                    f"Service trả về tuần {server_week}, không khớp tuần yêu cầu {tuan_num}"
                )
                sleep_s = min(
                    sleep_steps[min(attempt, len(sleep_steps) - 1)],
                    max(deadline - time.time(), 0),
                )
                attempt += 1
                if sleep_s > 0:
                    time.sleep(sleep_s)
                continue

            for row in payload.get("rows", []):
                if self._slot_row_matches(row, target_thu, target_buoi, target_tiet):
                    last_row = row
                    if row.get("has_data", False):
                        mon_hoc = str(row.get("mon_hoc", "")).strip()
                        return True, (
                            f"HTML fetch đã có dữ liệu ({mon_hoc or 'không rõ môn'})"
                        ), row
                    last_msg = "HTML fetch đã thấy row nhưng slot vẫn chưa có dữ liệu"
                    break

            sleep_s = min(
                sleep_steps[min(attempt, len(sleep_steps) - 1)],
                max(deadline - time.time(), 0),
            )
            attempt += 1
            if sleep_s > 0:
                time.sleep(sleep_s)

        return False, last_msg, last_row

    def wait_for_lesson_form(self, timeout_s=4.0, poll_interval=0.15):
        """Chờ popup chi tiết tiết học mở thật sự trước khi fill form."""
        deadline = time.time() + max(timeout_s, 0.5)
        last_msg = "Popup chi tiết tiết học chưa xuất hiện"

        while time.time() < deadline:
            try:
                result = self.page.evaluate('''() => {
                    if (typeof Ext === 'undefined' || !Ext.ComponentQuery) {
                        return {ok: false, msg: 'ExtJS not available'};
                    }
                    const wins = Ext.ComponentQuery.query('window');
                    for (const w of wins) {
                        try {
                            if (!(w.isVisible && w.isVisible())) continue;
                            const title = (w.title || '').toLowerCase();
                            const isLessonPopup = (
                                title.indexOf('chi ti') >= 0 ||
                                title.indexOf('ti\\u1EBFt h\\u1ECDc') >= 0 ||
                                title.indexOf('s\\u1ED5 \\u0111\\u1EA7u b\\u00E0i') >= 0 ||
                                title.indexOf('so dau bai') >= 0
                            );
                            if (!isLessonPopup) continue;
                            const form = w.down ? w.down('form') : null;
                            if (!form || !form.getForm) continue;
                            return {
                                ok: true,
                                title: w.title || '',
                                formId: form.id || '',
                            };
                        } catch (e) {}
                    }
                    return {ok: false, msg: 'popup_not_ready'};
                }''')
            except Exception as e:
                last_msg = f"Lỗi đọc popup: {type(e).__name__}: {str(e)[:80]}"
                time.sleep(poll_interval)
                continue

            if result.get("ok"):
                return True, result.get("title", "Popup chi tiết tiết học đã mở")

            last_msg = result.get("msg", last_msg)
            time.sleep(poll_interval)

        return False, last_msg

    def wait_for_lesson_form_closed(self, timeout_s=2.5, poll_interval=0.08):
        """Chờ popup chi tiết tiết học đóng hẳn sau save hoặc cleanup."""
        deadline = time.time() + max(timeout_s, 0.3)
        last_state = {"lessonOpen": True, "titles": []}

        while time.time() < deadline:
            try:
                state = self.page.evaluate('''() => {
                    if (typeof Ext === 'undefined' || !Ext.ComponentQuery) {
                        return {lessonOpen: false, titles: []};
                    }
                    const wins = Ext.ComponentQuery.query('window');
                    const titles = [];
                    let lessonOpen = false;
                    for (const w of wins) {
                        try {
                            if (!(w.isVisible && w.isVisible())) continue;
                            const title = String(w.title || '');
                            titles.push(title);
                            const tLow = title.toLowerCase();
                            if (
                                tLow.indexOf('chi ti') >= 0 ||
                                tLow.indexOf('ti\\u1EBFt h\\u1ECDc') >= 0 ||
                                tLow.indexOf('s\\u1ED5 \\u0111\\u1EA7u b\\u00E0i') >= 0 ||
                                tLow.indexOf('so dau bai') >= 0
                            ) {
                                lessonOpen = true;
                            }
                        } catch (e) {}
                    }
                    return {lessonOpen: lessonOpen, titles: titles};
                }''')
            except Exception as e:
                last_state = {"lessonOpen": True, "titles": [], "error": str(e)}
                time.sleep(poll_interval)
                continue

            last_state = state or last_state
            if not last_state.get("lessonOpen", False):
                return True, "Popup đã đóng"
            time.sleep(poll_interval)

        return False, f"Popup vẫn còn mở: {last_state.get('titles', [])}"

    def wait_for_form_ready_to_save(self, timeout_s=1.2, poll_interval=0.08):
        """Chờ form ổn định và valid trước khi bấm Lưu."""
        deadline = time.time() + max(timeout_s, 0.3)
        last_msg = "Form chưa ổn định để lưu"

        while time.time() < deadline:
            try:
                state = self.page.evaluate('''() => {
                    if (typeof Ext === 'undefined' || !Ext.ComponentQuery) {
                        return {ok: false, msg: 'ExtJS not available'};
                    }
                    const wins = Ext.ComponentQuery.query('window');
                    let formPanel = null;
                    for (const w of wins) {
                        try {
                            if (!(w.isVisible && w.isVisible())) continue;
                            const title = (w.title || '').toLowerCase();
                            if (
                                title.indexOf('chi ti') >= 0 ||
                                title.indexOf('ti\\u1EBFt h\\u1ECDc') >= 0 ||
                                title.indexOf('s\\u1ED5 \\u0111\\u1EA7u b\\u00E0i') >= 0 ||
                                title.indexOf('so dau bai') >= 0
                            ) {
                                const f = w.down ? w.down('form') : null;
                                if (f && f.getForm) {
                                    formPanel = f;
                                    break;
                                }
                            }
                        } catch (e) {}
                    }
                    if (!formPanel || !formPanel.getForm) {
                        return {ok: false, msg: 'popup_form_missing'};
                    }

                    let loadMaskVisible = false;
                    try {
                        const masks = document.querySelectorAll('.x-mask-msg, .x-mask-loading, .x-mask');
                        for (const mask of masks) {
                            if (mask.offsetParent !== null) {
                                loadMaskVisible = true;
                                break;
                            }
                        }
                    } catch (e2) {}

                    let comboLoading = false;
                    try {
                        const comboFields = formPanel.query('combobox');
                        for (const field of comboFields) {
                            const store = field.getStore ? field.getStore() : field.store;
                            if (store && store.isLoading && store.isLoading()) {
                                comboLoading = true;
                                break;
                            }
                        }
                    } catch (e3) {}

                    const form = formPanel.getForm();
                    let valid = false;
                    try { valid = !!form.isValid(); } catch (e4) {}

                    if (loadMaskVisible) {
                        return {ok: false, msg: 'loadmask_visible'};
                    }
                    if (comboLoading) {
                        return {ok: false, msg: 'combo_store_loading'};
                    }
                    if (!valid) {
                        return {ok: false, msg: 'form_invalid'};
                    }
                    return {ok: true, msg: 'ready'};
                }''')
            except Exception as e:
                last_msg = f"{type(e).__name__}: {str(e)[:80]}"
                time.sleep(poll_interval)
                continue

            if state.get("ok"):
                return True, state.get("msg", "ready")
            last_msg = state.get("msg", last_msg)
            time.sleep(poll_interval)

        return False, last_msg

    # -----------------------------------------------------------------
    # 3.4: CLICK NÚT ➕ (Mở form nhập liệu)
    # -----------------------------------------------------------------

    def click_add_button(self, row_index, row_dom_index=None):
        """Click đúng nút ➕ của row mục tiêu trên bảng.

        VnEdu dùng <a class="add add_tiet_so_dau_bai" onclick="themChiTietSoDauBai(this)">.
        Cách nhanh: lấy tất cả a.add_tiet_so_dau_bai → click theo index.

        Args:
            row_index: add_btn_index từ read_table() (0-based)
            row_dom_index: rowIdx từ read_table() nếu muốn click trực tiếp theo DOM row

        Returns:
            (success: bool, message: str)
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        try:
            result = self.page.evaluate('''(args) => {
                const targetIdx = args.targetIdx;
                const targetRowIdx = args.targetRowIdx;
                const addSelector = 'a.add_tiet_so_dau_bai, a[onclick*="themChiTietSoDauBai"]';

                function extractTietFromRow(tr) {
                    let tiet = '?';
                    if (!tr) return tiet;
                    const tds = tr.querySelectorAll('td');
                    for (const td of tds) {
                        const t = td.innerText.trim();
                        if (/^\\d+$/.test(t) && parseInt(t) >= 1 && parseInt(t) <= 5) {
                            tiet = t;
                            break;
                        }
                    }
                    return tiet;
                }

                // === Ưu tiên: click theo DOM row index đã parse ===
                let mainTable = document.querySelector('table.table');
                if (!mainTable) {
                    const tables = document.querySelectorAll('table');
                    let maxRowspan = 0;
                    for (const t of tables) {
                        const rs = t.querySelectorAll('td[rowspan]');
                        if (rs.length > maxRowspan) {
                            maxRowspan = rs.length;
                            mainTable = t;
                        }
                    }
                }

                if (mainTable && targetRowIdx !== null && targetRowIdx !== undefined && targetRowIdx >= 0) {
                    const allRows = mainTable.querySelectorAll('tr');
                    if (targetRowIdx >= 0 && targetRowIdx < allRows.length) {
                        const targetRow = allRows[targetRowIdx];
                        const rowBtn = targetRow.querySelector(addSelector);
                        if (rowBtn) {
                            const tiet = extractTietFromRow(targetRow);
                            rowBtn.click();
                            return {
                                ok: true,
                                tiet: tiet,
                                method: 'row_dom_index',
                                btnIndex: targetIdx,
                                rowIdx: targetRowIdx
                            };
                        }
                    }
                }

                // === Chiến lược chính: Dùng selector a.add_tiet_so_dau_bai ===
                const addLinks = document.querySelectorAll(addSelector);
                if (addLinks.length > 0) {
                    if (targetIdx < 0 || targetIdx >= addLinks.length) {
                        return {
                            ok: false,
                            error: 'Row index ' + targetIdx + ' ngoài phạm vi (total add buttons: ' + addLinks.length + ')'
                        };
                    }
                    const btn = addLinks[targetIdx];
                    const tr = btn.closest('tr');
                    const tiet = extractTietFromRow(tr);
                    btn.click();
                    return {
                        ok: true,
                        tiet: tiet,
                        method: 'add_btn_index',
                        btnIndex: targetIdx,
                        totalBtns: addLinks.length
                    };
                }

                // === Fallback: tìm bảng chính → duyệt rows ===
                if (!mainTable) return {ok: false, error: 'Không tìm thấy bảng'};

                const allBtns = mainTable.querySelectorAll(addSelector);
                if (targetIdx < 0 || targetIdx >= allBtns.length) {
                    return {
                        ok: false,
                        error: 'Row index ' + targetIdx + ' ngoài phạm vi (total: ' + allBtns.length + ')'
                    };
                }
                allBtns[targetIdx].click();
                return {ok: true, tiet: '?', btnIndex: targetIdx, totalBtns: allBtns.length};
            }''', {"targetIdx": row_index, "targetRowIdx": row_dom_index})

            if result.get("ok"):
                tiet = result.get("tiet", "?")
                total = result.get("totalBtns", "?")
                method = result.get("method", "?")
                logger.info(
                    f"Clicked add button #{row_index} (rowIdx={row_dom_index}, "
                    f"tiết {tiet}, total={total}, method={method})"
                )

                return True, f"Đã click tiết {tiet}"
            else:
                return False, result.get("error", "Unknown")

        except PlaywrightTimeout:
            return False, "Timeout click nút ➕"
        except Exception as e:
            return False, f"Lỗi click nút: {type(e).__name__}: {str(e)[:80]}"

    # -----------------------------------------------------------------
    # 3.5: FORM OPERATIONS (Nhập liệu + Lưu)
    # -----------------------------------------------------------------

    def inspect_form(self):
        """Khám phá cấu trúc form nhập liệu đang mở.

        Gọi SAU KHI click ➕ để xem form có những field gì.
        Kết quả dùng để debug và tinh chỉnh selectors.

        Returns:
            (success, dict) — form structure info
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        try:
            result = self.page.evaluate('''() => {
                const formInfo = {
                    selects: [],
                    inputs: [],
                    textareas: [],
                    buttons: [],
                    modals: [],
                };

                // Scan tất cả visible form elements
                for (const sel of document.querySelectorAll('select')) {
                    if (sel.offsetParent === null) continue;  // Skip hidden
                    const opts = Array.from(sel.options).map(o => ({
                        value: o.value, text: o.text.trim()
                    })).slice(0, 20);
                    formInfo.selects.push({
                        id: sel.id,
                        name: sel.name,
                        options: opts,
                        value: sel.value,
                        label: sel.closest('td, tr')?.querySelector('label, span')?.innerText?.trim()?.substring(0, 50) || ''
                    });
                }

                for (const inp of document.querySelectorAll('input[type="text"], input:not([type])')) {
                    if (inp.offsetParent === null) continue;
                    formInfo.inputs.push({
                        id: inp.id,
                        name: inp.name,
                        value: inp.value,
                        placeholder: inp.placeholder || '',
                        label: inp.closest('td, tr')?.querySelector('label, span')?.innerText?.trim()?.substring(0, 50) || ''
                    });
                }

                for (const ta of document.querySelectorAll('textarea')) {
                    if (ta.offsetParent === null) continue;
                    formInfo.textareas.push({
                        id: ta.id,
                        name: ta.name,
                        value: ta.value,
                        label: ta.closest('td, tr')?.querySelector('label, span')?.innerText?.trim()?.substring(0, 50) || ''
                    });
                }

                for (const btn of document.querySelectorAll(
                    'input[type="button"], input[type="submit"], button, a.btn'
                )) {
                    if (btn.offsetParent === null) continue;
                    formInfo.buttons.push({
                        tag: btn.tagName,
                        id: btn.id,
                        text: (btn.value || btn.innerText || '').trim().substring(0, 30),
                        type: btn.type || ''
                    });
                }

                // Detect modals/dialogs
                for (const m of document.querySelectorAll(
                    '.modal, [role="dialog"], .popup, .ui-dialog, div[id*="pnl"], div[id*="Panel"]'
                )) {
                    if (m.offsetParent === null) continue;
                    formInfo.modals.push({
                        tag: m.tagName,
                        id: m.id,
                        className: (m.className || '').substring(0, 60),
                        visible: true
                    });
                }

                return formInfo;
            }''')

            return True, result

        except Exception as e:
            return False, f"Lỗi inspect form: {type(e).__name__}: {str(e)[:80]}"

    def _fetch_ten_bai_by_ppct(self, ppct, mon_hoc_value, phan_mon_value):
        """Gọi service getPPCT của VnEdu trong page context hiện tại."""
        try:
            result = self.page.evaluate(
                '''async (args) => {
                    try {
                        const token = typeof myToken !== 'undefined'
                            ? (typeof myToken === 'function' ? myToken() : myToken)
                            : '';
                        const userId = typeof myUserId !== 'undefined'
                            ? (typeof myUserId === 'function' ? myUserId() : myUserId)
                            : '';
                        const namHoc = String(
                            typeof phpviet_nam_hoc_v5 !== 'undefined' ? phpviet_nam_hoc_v5 : 2025
                        );
                        let lopHocId = '';
                        try {
                            const lopCombo = Ext.ComponentQuery.query('combobox').find(function(combo) {
                                const comboName = combo.getName ? combo.getName() : (combo.name || '');
                                return comboName === 'cboLopHoc';
                            });
                            if (lopCombo) {
                                lopHocId = String(
                                    lopCombo.getValue ? (lopCombo.getValue() || '') : (lopCombo.value || '')
                                );
                            }
                        } catch (eLop) {}

                        const params = new URLSearchParams();
                        params.set('my_token', String(token || ''));
                        params.set('my_user_id', String(userId || ''));
                        params.set('app_nam_hoc', namHoc);
                        params.set('mon_hoc_id', String(args.monHocValue || ''));
                        params.set('phan_mon_id', String(args.phanMonValue || ''));
                        params.set('lop_hoc_id', String(lopHocId || ''));
                        params.set('tiet_ppct', String(args.ppct || ''));

                        const url = './?call=app.sodaubai.serv.so_dau_bai.getPPCT'
                            + '&app_nam_hoc=' + encodeURIComponent(namHoc)
                            + '&my_token=' + encodeURIComponent(String(token || ''));

                        const resp = await fetch(url, {
                            method: 'POST',
                            headers: {'content-type': 'application/x-www-form-urlencoded; charset=UTF-8'},
                            body: params.toString(),
                            credentials: 'same-origin',
                        });
                        const rawText = await resp.text();
                        let data = null;
                        try {
                            data = JSON.parse(rawText);
                        } catch (eJson) {
                            return {ok: false, error: 'json_parse', rawText: rawText.slice(0, 200)};
                        }
                        const tenBai = data && data.data && data.data.ten_bai
                            ? String(data.data.ten_bai).trim()
                            : '';
                        if (tenBai) {
                            return {ok: true, ten_bai: tenBai};
                        }
                        return {ok: false, error: 'empty_data', payload: data};
                    } catch (eFetch) {
                        return {
                            ok: false,
                            error: String(eFetch && eFetch.message ? eFetch.message : eFetch),
                        };
                    }
                }''',
                {
                    "ppct": str(ppct),
                    "monHocValue": "" if mon_hoc_value is None else str(mon_hoc_value),
                    "phanMonValue": "" if phan_mon_value is None else str(phan_mon_value),
                },
            )
            if result.get("ok"):
                return True, result.get("ten_bai", "")
            return False, result.get("error", "empty_data")
        except Exception as e:
            return False, f"{type(e).__name__}: {str(e)[:80]}"

    def _set_popup_plain_field(self, field_name, value):
        """Set 1 field text/textarea/number trên popup lesson hiện tại."""
        try:
            result = self.page.evaluate(
                '''(args) => {
                    if (!(window.Ext && Ext.ComponentQuery)) {
                        return {ok: false, error: 'ExtJS not available'};
                    }
                    const wins = Ext.ComponentQuery.query('window');
                    for (const w of wins) {
                        try {
                            if (!(w.isVisible && w.isVisible())) continue;
                            const title = String(w.title || '').toLowerCase();
                            if (title.indexOf('chi ti') < 0 && title.indexOf('ti\\u1ebft h\\u1ecdc') < 0) continue;
                            const formPanel = w.down ? w.down('form') : null;
                            if (!formPanel || !formPanel.getForm) continue;
                            const form = formPanel.getForm();
                            const field = form.findField(args.fieldName);
                            if (!field) {
                                return {ok: false, error: 'Field not found: ' + args.fieldName};
                            }
                            field.setValue(args.value);
                            try { field.fireEvent('change', field, args.value, field.originalValue); } catch (e1) {}
                            try { field.fireEvent('blur', field); } catch (e2) {}
                            try { field.validate && field.validate(); } catch (e3) {}
                            return {ok: true};
                        } catch (e) {}
                    }
                    return {ok: false, error: 'popup_not_found'};
                }''',
                {"fieldName": str(field_name), "value": str(value)},
            )
            return bool(result.get("ok")), result.get("error", "")
        except Exception as e:
            return False, f"{type(e).__name__}: {str(e)[:80]}"

    def _native_retype_ppct_and_wait_noi_dung(
        self,
        ppct,
        mon_hoc_value=None,
        phan_mon_value=None,
        timeout_s=1.8,
    ):
        """Gõ thật PPCT bằng keyboard để kích hoạt rule auto-fill của VnEdu."""
        try:
            info = self.page.evaluate(
                '''() => {
                    if (!(window.Ext && Ext.ComponentQuery)) {
                        return {ok: false, error: 'ExtJS not available'};
                    }
                    const wins = Ext.ComponentQuery.query('window');
                    for (const w of wins) {
                        try {
                            if (!(w.isVisible && w.isVisible())) continue;
                            const title = String(w.title || '').toLowerCase();
                            if (title.indexOf('chi ti') < 0 && title.indexOf('ti\\u1ebft h\\u1ecdc') < 0) continue;
                            const formPanel = w.down ? w.down('form') : null;
                            if (!formPanel || !formPanel.getForm) continue;
                            const form = formPanel.getForm();
                            const ppct = form.findField('tiet_ppct');
                            const noi = form.findField('noi_dung');
                            return {
                                ok: !!(ppct && ppct.inputEl && ppct.inputEl.dom && noi),
                                ppctInputId: ppct && ppct.inputEl && ppct.inputEl.dom ? ppct.inputEl.dom.id : '',
                                currentPpct: ppct && ppct.getValue ? ppct.getValue() : '',
                                currentNoiDung: noi && noi.getValue ? noi.getValue() : '',
                            };
                        } catch (e) {}
                    }
                    return {ok: false, error: 'popup_not_found'};
                }'''
            )
            if not info.get("ok"):
                return False, f"Không tìm thấy input PPCT thật: {info.get('error', 'unknown')}", []

            selector = f"#{info.get('ppctInputId', '')}"
            target_text = str(ppct)
            locator = self.page.locator(selector)
            locator.click()
            try:
                self.page.keyboard.press("Control+A")
            except Exception:
                pass
            self.page.keyboard.press("Backspace")
            time.sleep(0.18)
            self.page.keyboard.type(target_text, delay=120)
            self.page.keyboard.press("Tab")

            deadline = time.time() + max(timeout_s, 0.5)
            while time.time() < deadline:
                state = self.page.evaluate(
                    '''() => {
                        if (!(window.Ext && Ext.ComponentQuery)) return {ok: false};
                        const wins = Ext.ComponentQuery.query('window');
                        for (const w of wins) {
                            try {
                                if (!(w.isVisible && w.isVisible())) continue;
                                const title = String(w.title || '').toLowerCase();
                                if (title.indexOf('chi ti') < 0 && title.indexOf('ti\\u1ebft h\\u1ecdc') < 0) continue;
                                const formPanel = w.down ? w.down('form') : null;
                                if (!formPanel || !formPanel.getForm) continue;
                                const form = formPanel.getForm();
                                const ppct = form.findField('tiet_ppct');
                                const noi = form.findField('noi_dung');
                                return {
                                    ok: true,
                                    ppct: ppct && ppct.getValue ? ppct.getValue() : '',
                                    noi_dung: noi && noi.getValue ? String(noi.getValue() || '') : '',
                                };
                            } catch (e) {}
                        }
                        return {ok: false};
                    }'''
                )
                noi_dung = str(state.get("noi_dung", "")).strip()
                if noi_dung and noi_dung != "---":
                    return True, noi_dung, [
                        f"tiet_ppct: native-typed = {target_text}",
                        f"noi_dung: auto-filled = {noi_dung[:60]}",
                    ]
                time.sleep(0.08)

            ok_fetch, ten_bai = self._fetch_ten_bai_by_ppct(
                ppct,
                mon_hoc_value=mon_hoc_value,
                phan_mon_value=phan_mon_value,
            )
            if ok_fetch and ten_bai:
                ok_set, set_err = self._set_popup_plain_field("noi_dung", ten_bai)
                if ok_set:
                    return True, ten_bai, [
                        f"tiet_ppct: native-typed = {target_text}",
                        f"noi_dung: fetched_by_getPPCT = {ten_bai[:60]}",
                    ]
                return False, f"Không set được tên bài từ getPPCT: {set_err}", []

            return False, (
                "VnEdu không tự hiện tên bài sau khi nhập PPCT "
                "và getPPCT cũng không trả dữ liệu"
            ), [f"tiet_ppct: native-typed = {target_text}"]
        except Exception as e:
            return False, f"{type(e).__name__}: {str(e)[:80]}", []

    def fill_form(self, ppct, hs_nghi, nhan_xet, diem, phan_mon_index=None,
                 xep_loai=None, noi_dung=None, mon_hoc_index=None,
                 phan_mon_text=None, mon_hoc_text=None, mon_hoc_field=None):
        """Nhập liệu vào ExtJS form popup đang mở.

        VnEdu v5 dùng ExtJS 4.x form panel — tất cả field là ExtJS component.
        Dùng Ext form API: form.getForm().findField(name).setValue(value).

        Thứ tự fields trên VnEdu form (chỉ điền các field cần thiết):
        0. mon_hoc_id (combobox) — Môn học (optional, set trước Phân môn)
        1. phan_mon_id (combobox) — chọn theo value nếu cung cấp
        2. tiet_ppct (numberfield) — tiết PPCT
        3. soluong_nghi (numberfield) — số HS nghỉ
        4. noi_dung (textareafield) — tên bài / nội dung (BẮT BUỘC)
        5. nhan_xet (textareafield) — nhận xét giáo viên
        6. diem (numberfield) — điểm tiết học
        7. xep_loai (combobox) — xếp loại (optional)

        Args:
            ppct: str — giá trị tiết PPCT
            hs_nghi: str — số HS nghỉ
            nhan_xet: str — nhận xét giáo viên
            diem: str — điểm tiết học
            phan_mon_index: str|int|None — value Phân môn combobox (None = giữ nguyên)
            xep_loai: str|None — value Xếp loại combobox (None = giữ nguyên)
            noi_dung: str|None — tên bài / nội dung (None = auto-fill check)
            mon_hoc_index: str|int|None — value Môn học combobox (None = giữ nguyên)
            phan_mon_text: str|None — text Phân môn để verify sau khi set
            mon_hoc_text: str|None — text Môn học để verify sau khi set
            mon_hoc_field: str|None — tên field Môn học đã discover khi scan

        Returns:
            (success: bool, message: str)
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        try:
            mon_hoc_candidates = []
            if mon_hoc_field:
                mon_hoc_candidates.append(str(mon_hoc_field))
            for name in (
                "mon_hoc_id", "mon_hoc", "monhoc_id", "monhoc",
                "subject_id", "ma_mon_hoc"
            ):
                if name not in mon_hoc_candidates:
                    mon_hoc_candidates.append(name)

            # === Pre-fill: EXPAND combobox dropdowns để trigger store load ===
            # VnEdu popup có lazy-loaded combobox stores — store chỉ load khi
            # user mở dropdown lần đầu. Phải expand() trước → chờ store load
            # → rồi mới setValue() được.
            _expand_combos_js = '''() => {
                if (typeof Ext === 'undefined') return {ok: false, msg: 'no_ext'};

                // Tìm popup form "chi tiết tiết học"
                var wins = Ext.ComponentQuery.query('window');
                var popupForm = null;
                for (var i = 0; i < wins.length; i++) {
                    var w = wins[i];
                    if (!w.isVisible || !w.isVisible()) continue;
                    var title = (w.title || '').toLowerCase();
                    if (title.indexOf('chi ti') >= 0 ||
                        title.indexOf('ti\\u1EBFt h\\u1ECDc') >= 0) {
                        var fp = w.down('form');
                        if (fp && fp.getForm) { popupForm = fp.getForm(); break; }
                    }
                }
                if (!popupForm) return {ok: false, msg: 'popup_not_found'};

                // Tìm các combobox cần expand (store rỗng)
                var comboNames = ['mon_hoc_id', 'phan_mon_id', 'xep_loai'];
                var result = {};
                for (var ci = 0; ci < comboNames.length; ci++) {
                    var cname = comboNames[ci];
                    var field = popupForm.findField(cname);
                    if (!field) { result[cname] = 'not_found'; continue; }

                    // Kiểm tra store hiện tại
                    var store = null;
                    try { store = field.getStore ? field.getStore() : field.store; } catch(eg) {}
                    if (!store) {
                        try { store = field.store; } catch(es) {}
                    }
                    var count = 0;
                    try { count = store ? store.getCount() : 0; } catch(ec) {}

                    if (count > 0) {
                        result[cname] = 'loaded=' + count;
                        continue;
                    }

                    // Store rỗng → Strategy 1: bindStore từ page-level combo
                    var bound = false;
                    try {
                        var allCombos = Ext.ComponentQuery.query('combobox');
                        for (var j = 0; j < allCombos.length; j++) {
                            var src = allCombos[j];
                            if (src === field) continue;
                            var srcName = '';
                            try { srcName = src.getName ? src.getName() : ''; } catch(en) {}
                            if (srcName !== cname) continue;
                            var srcStore = src.getStore ? src.getStore() : null;
                            if (!srcStore || srcStore.getCount() === 0) continue;

                            // Tìm thấy source → bindStore (chia sẻ store)
                            try {
                                field.bindStore(srcStore, true);
                                var newCount = field.getStore().getCount();
                                if (newCount > 0) {
                                    result[cname] = 'bind_ok=' + newCount + ' from #' + src.id;
                                    bound = true;
                                }
                            } catch(eb) {
                                result[cname] = 'bind_err=' + eb.message;
                            }
                            break;
                        }
                    } catch(eAll) {
                        result[cname] = 'search_err=' + eAll.message;
                    }

                    // Strategy 2: Expand dropdown để trigger internal load
                    if (!bound) {
                        try {
                            field.expand();
                            result[cname] = 'expanded';
                        } catch(ex) {
                            result[cname] = 'expand_err=' + ex.message;
                        }
                    }
                }
                return {ok: true, combos: result};
            }'''

            # Bước 1: Expand/bind combos
            # Khởi tạo trước để tránh NameError nếu evaluate ném exception
            # (popup đóng sớm / "Execution context was destroyed").
            expand_result = None
            try:
                expand_result = self.page.evaluate(_expand_combos_js)
                logger.info(f"Pre-fill expand: {expand_result}")
            except Exception as e_exp:
                logger.debug(f"Pre-fill expand error: {e_exp}")

            # Bước 2: Chờ store load nếu combo thật sự còn lazy-load.
            combo_states = {}
            if isinstance(expand_result, dict):
                combo_states = expand_result.get("combos", {}) or {}
            expanded_combo_names = [
                name for name, state in combo_states.items()
                if str(state).startswith("expanded")
            ]
            needs_combo_wait = any(
                str(state).startswith("expanded")
                for state in combo_states.values()
            )

            # Bước 3: Collapse any expanded combos + verify store counts
            _collapse_verify_js = '''() => {
                if (typeof Ext === 'undefined') return {ok: false};
                var wins = Ext.ComponentQuery.query('window');
                var popupForm = null;
                for (var i = 0; i < wins.length; i++) {
                    var w = wins[i];
                    if (!w.isVisible || !w.isVisible()) continue;
                    var title = (w.title || '').toLowerCase();
                    if (title.indexOf('chi ti') >= 0 ||
                        title.indexOf('ti\\u1EBFt h\\u1ECDc') >= 0) {
                        var fp = w.down('form');
                        if (fp && fp.getForm) { popupForm = fp.getForm(); break; }
                    }
                }
                if (!popupForm) return {ok: false, msg: 'no_form'};

                var comboNames = ['mon_hoc_id', 'phan_mon_id', 'xep_loai'];
                var stores = {};
                for (var ci = 0; ci < comboNames.length; ci++) {
                    var cname = comboNames[ci];
                    var field = popupForm.findField(cname);
                    if (!field) { stores[cname] = -1; continue; }
                    // Collapse nếu đang mở
                    try { if (field.isExpanded) field.collapse(); } catch(ec) {}
                    // Đọc store count
                    var count = 0;
                    try {
                        var st = field.getStore ? field.getStore() : field.store;
                        count = st ? st.getCount() : 0;
                    } catch(e) {}
                    stores[cname] = count;
                }
                return {ok: true, stores: stores};
            }'''

            try:
                verify_result = None
                deadline = time.time() + (0.9 if needs_combo_wait else 0.2)
                while time.time() < deadline:
                    verify_result = self.page.evaluate(_collapse_verify_js)
                    stores = {}
                    if isinstance(verify_result, dict):
                        stores = verify_result.get("stores", {}) or {}
                    if not expanded_combo_names:
                        break
                    all_ready = True
                    for combo_name in expanded_combo_names:
                        count = int(stores.get(combo_name, 0) or 0)
                        if count <= 0:
                            all_ready = False
                            break
                    if all_ready:
                        break
                    time.sleep(0.06)
                logger.info(f"Pre-fill verify: {verify_result}")
            except Exception as e_ver:
                logger.debug(f"Pre-fill verify error: {e_ver}")

            payload = {
                "ppct": str(ppct),
                "hsNghi": str(hs_nghi),
                "nhanXet": str(nhan_xet),
                "diem": str(diem),
                "phanMonVal": (
                    str(phan_mon_index) if phan_mon_index is not None else None
                ),
                "phanMonText": (
                    str(phan_mon_text) if phan_mon_text is not None else None
                ),
                "xepLoaiVal": str(xep_loai) if xep_loai is not None else None,
                "noiDung": str(noi_dung) if noi_dung is not None else None,
                "monHocVal": (
                    str(mon_hoc_index) if mon_hoc_index is not None else None
                ),
                "monHocText": (
                    str(mon_hoc_text) if mon_hoc_text is not None else None
                ),
                "monHocCandidates": mon_hoc_candidates,
                "autoNoiDung": (
                    noi_dung == "---" or
                    noi_dung is None or
                    noi_dung == ""
                ),
            }

            verified_result = self.page.evaluate('''async (args) => {
                const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

                function normalize(value) {
                    try {
                        return String(value == null ? '' : value)
                            .normalize('NFD')
                            .replace(/[\\u0300-\\u036f]/g, '')
                            .trim()
                            .toLowerCase();
                    } catch (e) {
                        return String(value == null ? '' : value).trim().toLowerCase();
                    }
                }

                function findPopupFormPanel() {
                    let formPanel = null;
                    try {
                        const wins = Ext.ComponentQuery.query('window');
                        for (const w of wins) {
                            if (!(w.isVisible && w.isVisible())) continue;
                            const title = (w.title || '').toLowerCase();
                            if (title.indexOf('chi ti') >= 0 ||
                                title.indexOf('ti\\u1EBFt h\\u1ECDc') >= 0) {
                                const f = w.down('form');
                                if (f) {
                                    formPanel = f;
                                    break;
                                }
                            }
                        }
                    } catch (e) {}

                    if (!formPanel) {
                        try {
                            const allForms = Ext.ComponentQuery.query('form');
                            for (const f of allForms) {
                                if (f.isVisible && f.isVisible() && f.getForm) {
                                    formPanel = f;
                                    break;
                                }
                            }
                        } catch (e2) {}
                    }
                    return formPanel;
                }

                function getFieldStore(field) {
                    if (!field) return null;
                    try {
                        if (field.getStore && typeof field.getStore === 'function') {
                            return field.getStore();
                        }
                    } catch (e) {}
                    try {
                        return field.store || null;
                    } catch (e2) {
                        return null;
                    }
                }

                function getStoreCount(store) {
                    if (!store) return 0;
                    try { return store.getCount ? store.getCount() : 0; } catch (e) { return 0; }
                }

                function getFieldName(field) {
                    if (!field) return '';
                    try { return field.getName ? field.getName() : (field.name || ''); } catch (e) {}
                    return field.name || '';
                }

                function getComboRawText(field) {
                    if (!field) return '';
                    try { if (field.getRawValue) return String(field.getRawValue() || ''); } catch (e) {}
                    try {
                        if (field.inputEl && field.inputEl.dom) {
                            return String(field.inputEl.dom.value || '');
                        }
                    } catch (e2) {}
                    return '';
                }

                function isComboField(field) {
                    if (!field) return false;
                    try {
                        const xtype = field.getXType ? field.getXType() : (field.xtype || '');
                        if (xtype === 'combobox' || xtype === 'combo') return true;
                    } catch (e) {}
                    try { return !!(field.getStore && typeof field.getStore === 'function'); } catch (e2) {}
                    return false;
                }

                function getComboOptions(field) {
                    const options = [];
                    if (!field) return options;
                    const store = getFieldStore(field);
                    if (!store) return options;
                    const valueField = field.valueField || 'id';
                    const displayField = field.displayField || 'name';
                    try {
                        store.each(function(record) {
                            const text = (
                                record.get(displayField) != null
                                    ? record.get(displayField)
                                    : (record.get('name') || record.get('ten') || '')
                            );
                            options.push({
                                value: String(record.get(valueField) != null ? record.get(valueField) : ''),
                                text: String(text != null ? text : '')
                            });
                        });
                    } catch (e) {}
                    return options;
                }

                function buildOptionsSignature(options) {
                    return (options || [])
                        .slice(0, 30)
                        .map(opt => String(opt.value || '') + '|' + String(opt.text || ''))
                        .join('||');
                }

                function findFieldByCandidates(form, candidates, labelKeywords) {
                    const tried = candidates || [];
                    for (let i = 0; i < tried.length; i++) {
                        try {
                            const direct = form.findField(tried[i]);
                            if (direct) return direct;
                        } catch (e) {}
                    }

                    try {
                        const fields = form.getFields().items || [];
                        for (let i = 0; i < fields.length; i++) {
                            const field = fields[i];
                            const label = normalize(field.fieldLabel || field.boxLabel || '');
                            for (let j = 0; j < (labelKeywords || []).length; j++) {
                                if (label.indexOf(labelKeywords[j]) >= 0) {
                                    return field;
                                }
                            }
                        }
                    } catch (e2) {}
                    return null;
                }

                function findRecordInStore(store, value, expectedText, field) {
                    if (!store) return null;
                    const valueField = field.valueField || 'id';
                    const displayField = field.displayField || 'name';
                    let record = null;

                    try {
                        if (store.findRecord) record = store.findRecord(valueField, value);
                    } catch (e) {}

                    if (!record) {
                        const intValue = parseInt(value, 10);
                        if (!isNaN(intValue)) {
                            try {
                                if (store.findRecord) record = store.findRecord(valueField, intValue);
                            } catch (e2) {}
                        }
                    }

                    if (!record && expectedText) {
                        const expectedNorm = normalize(expectedText);
                        try {
                            store.each(function(rec) {
                                if (record) return;
                                const text = rec.get(displayField) != null
                                    ? rec.get(displayField)
                                    : (rec.get('name') || rec.get('ten') || '');
                                if (normalize(text) === expectedNorm) {
                                    record = rec;
                                }
                            });
                        } catch (e3) {}
                    }
                    return record;
                }

                async function ensureComboStoreLoaded(field, timeoutMs) {
                    if (!field) {
                        return {ok: false, count: 0, diag: 'field_missing'};
                    }

                    let lastDiag = '';
                    const start = Date.now();
                    while ((Date.now() - start) < timeoutMs) {
                        let store = getFieldStore(field);
                        let count = getStoreCount(store);
                        if (count > 0) {
                            try { if (field.isExpanded) field.collapse(); } catch (e) {}
                            return {ok: true, count: count, diag: lastDiag || 'loaded'};
                        }

                        const fieldName = getFieldName(field);
                        try {
                            const allCombos = Ext.ComponentQuery.query('combobox');
                            for (let i = 0; i < allCombos.length; i++) {
                                const srcCombo = allCombos[i];
                                if (srcCombo === field) continue;
                                let srcName = '';
                                try {
                                    srcName = srcCombo.getName ? srcCombo.getName() : (srcCombo.name || '');
                                } catch (eName) {}
                                if (!fieldName || srcName !== fieldName) continue;

                                const srcStore = getFieldStore(srcCombo);
                                const srcCount = getStoreCount(srcStore);
                                if (!srcStore || srcCount <= 0) continue;

                                try {
                                    field.bindStore(srcStore, true);
                                    store = getFieldStore(field);
                                    count = getStoreCount(store);
                                    if (count > 0) {
                                        return {
                                            ok: true,
                                            count: count,
                                            diag: 'bind:' + (srcCombo.id || srcName || '?')
                                        };
                                    }
                                } catch (bindErr) {
                                    lastDiag = 'bind_err:' + bindErr.message;
                                }
                            }
                        } catch (allErr) {
                            lastDiag = 'search_err:' + allErr.message;
                        }

                        try { if (field.expand) field.expand(); } catch (expandErr) {
                            lastDiag = 'expand_err:' + expandErr.message;
                        }
                        await sleep(150);
                    }

                    const available = getComboOptions(field);
                    return {
                        ok: available.length > 0,
                        count: available.length,
                        diag: lastDiag || 'timeout',
                        available: available.slice(0, 20)
                    };
                }

                async function waitForDependentCombo(parentField, childField, expectedParentValue, timeoutMs) {
                    if (!parentField || !childField) {
                        return {ok: false, count: 0, available: []};
                    }

                    let lastSignature = '';
                    let stableCount = 0;
                    const start = Date.now();
                    while ((Date.now() - start) < timeoutMs) {
                        await ensureComboStoreLoaded(childField, 400);

                        let parentValue = '';
                        try { parentValue = String(parentField.getValue() || ''); } catch (e) {}
                        const options = getComboOptions(childField);
                        let loading = false;
                        try {
                            const childStore = getFieldStore(childField);
                            loading = !!(childStore && childStore.isLoading && childStore.isLoading());
                        } catch (e2) {}

                        const signature = options
                            .slice(0, 20)
                            .map(opt => opt.value + '|' + opt.text)
                            .join('||');

                        if (parentValue === String(expectedParentValue) && !loading) {
                            stableCount = (signature && signature === lastSignature)
                                ? stableCount + 1
                                : 1;
                            if (stableCount >= 2) {
                                return {ok: true, count: options.length, available: options.slice(0, 20)};
                            }
                        } else {
                            stableCount = 0;
                        }

                        lastSignature = signature;
                        await sleep(150);
                    }

                    const available = getComboOptions(childField);
                    return {ok: false, count: available.length, available: available.slice(0, 20)};
                }

                async function setComboField(field, value, label, config, log, errors) {
                    const cfg = config || {};
                    if (value === null || value === undefined || value === '') {
                        log.push(label + ': skipped');
                        return {ok: true, skipped: true};
                    }

                    if (!field || !isComboField(field)) {
                        const msg = label + ': field not found';
                        log.push(msg);
                        if (cfg.required) errors.push(msg);
                        return {ok: false};
                    }

                    const storeInfo = await ensureComboStoreLoaded(field, 2200);
                    if (!storeInfo.ok) {
                        const msg = label + ': store empty (' + (storeInfo.diag || '?') + ')';
                        log.push(msg);
                        if (cfg.required) errors.push(msg);
                        return {ok: false};
                    }

                    const store = getFieldStore(field);
                    const record = findRecordInStore(store, value, cfg.expectedText, field);
                    if (!record) {
                        const options = getComboOptions(field)
                            .slice(0, 10)
                            .map(opt => opt.text)
                            .join(', ');
                        const msg = label + ': option not found value=' + String(value) +
                            (cfg.expectedText ? ' text=' + cfg.expectedText : '') +
                            (options ? ' | available=' + options : '');
                        log.push(msg);
                        if (cfg.required) errors.push(msg);
                        return {ok: false};
                    }

                    const valueField = field.valueField || 'id';
                    const displayField = field.displayField || 'name';
                    const expectedValue = String(record.get(valueField) != null ? record.get(valueField) : '');
                    const expectedDisplay = String(
                        record.get(displayField) != null
                            ? record.get(displayField)
                            : (cfg.expectedText || '')
                    );

                    field.setValue(record.get(valueField));
                    try { field.lastSelection = [record]; } catch (eLast) {}
                    try { field.fireEvent('select', field, [record]); } catch (eSelect) {}
                    try { field.validate(); } catch (eValidate) {}

                    if (cfg.waitAfterMs) {
                        await sleep(cfg.waitAfterMs);
                    }

                    let afterValue = '';
                    try { afterValue = String(field.getValue() || ''); } catch (eAfter) {}
                    const afterText = getComboRawText(field);
                    const valueOk = afterValue === expectedValue;
                    const textOk = !cfg.expectedText ||
                        normalize(afterText) === normalize(cfg.expectedText) ||
                        normalize(afterText) === normalize(expectedDisplay);

                    log.push(
                        label + ': combo ' + expectedValue + ' -> ' + afterValue +
                        ' [' + (afterText || expectedDisplay) + ']'
                    );

                    if (!valueOk || !textOk) {
                        const msg = label + ': verify failed expected=' + expectedValue +
                            (cfg.expectedText ? '/' + cfg.expectedText : '') +
                            ' got=' + afterValue + '/' + afterText;
                        if (cfg.required) errors.push(msg);
                        return {ok: false};
                    }

                    return {
                        ok: true,
                        value: afterValue,
                        text: afterText || expectedDisplay,
                        fieldName: getFieldName(field)
                    };
                }

                function setPlainField(form, fieldName, value, label, required, log, errors) {
                    if (value === null || value === undefined || value === '') {
                        log.push(label + ': skipped');
                        return false;
                    }

                    try {
                        const field = form.findField(fieldName);
                        if (!field) {
                            log.push(label + ': NOT FOUND (' + fieldName + ')');
                            if (required) errors.push('Field not found: ' + fieldName);
                            return false;
                        }

                        let xtype = '?';
                        try { xtype = field.getXType ? field.getXType() : (field.xtype || '?'); } catch (e) {}

                        field.setValue(value);
                        try {
                            field.fireEvent('change', field, value, field.originalValue);
                            field.fireEvent('blur', field);
                        } catch (e2) {}

                        log.push(
                            label + ': OK [' + xtype + '#' + (field.id || '?') + '] = ' +
                            String(value).substring(0, 30)
                        );
                        return true;
                    } catch (e3) {
                        log.push(label + ': ERROR ' + e3.message);
                        if (required) errors.push(label + ': ' + e3.message);
                        return false;
                    }
                }

                if (typeof Ext === 'undefined' || !Ext.ComponentQuery) {
                    return {ok: false, errors: ['ExtJS not available'], log: []};
                }

                const formPanel = findPopupFormPanel();
                if (!formPanel || !formPanel.getForm) {
                    return {ok: false, errors: ['Khong tim thay ExtJS form popup'], log: []};
                }

                const form = formPanel.getForm();
                const log = [];
                const errors = [];

                const monField = findFieldByCandidates(form, args.monHocCandidates || [], ['mon hoc']);
                const phanField = findFieldByCandidates(form, ['phan_mon_id'], ['phan mon']);
                const xepLoaiField = findFieldByCandidates(form, ['xep_loai'], ['xep loai']);

                if (args.monHocVal !== null && args.monHocVal !== undefined && args.monHocVal !== '') {
                    const monResult = await setComboField(
                        monField,
                        args.monHocVal,
                        'mon_hoc',
                        {
                            required: true,
                            expectedText: args.monHocText || '',
                            waitAfterMs: 150
                        },
                        log,
                        errors
                    );

                    if (monResult.ok && args.phanMonVal !== null && args.phanMonVal !== undefined &&
                            args.phanMonVal !== '' && phanField) {
                        const waitResult = await waitForDependentCombo(
                            monField,
                            phanField,
                            monResult.value,
                            2600
                        );
                        log.push(
                            'phan_mon_store: ' +
                            (waitResult.ok ? 'ready=' + waitResult.count : 'timeout=' + waitResult.count)
                        );
                    }
                } else {
                    log.push('mon_hoc: skipped (no value)');
                }

                if (args.phanMonVal !== null && args.phanMonVal !== undefined && args.phanMonVal !== '') {
                    await setComboField(
                        phanField,
                        args.phanMonVal,
                        'phan_mon',
                        {
                            required: true,
                            expectedText: args.phanMonText || '',
                            waitAfterMs: 100
                        },
                        log,
                        errors
                    );
                } else {
                    log.push('phan_mon: skipped (no value)');
                }

                const needsAutoNoiDung = (
                    !!args.autoNoiDung
                );

                if (needsAutoNoiDung) {
                    log.push('tiet_ppct: deferred_native_typing = ' + String(args.ppct));
                    log.push('noi_dung: deferred_native_autofill');
                } else {
                    setPlainField(form, 'tiet_ppct', args.ppct, 'tiet_ppct', true, log, errors);
                }
                setPlainField(form, 'soluong_nghi', args.hsNghi, 'soluong_nghi', false, log, errors);

                if (needsAutoNoiDung) {
                    // noi_dung đã được xử lý cùng lúc với sự kiện của tiet_ppct.
                } else {
                    setPlainField(form, 'noi_dung', args.noiDung, 'noi_dung', true, log, errors);
                }

                setPlainField(form, 'nhan_xet', args.nhanXet, 'nhan_xet', false, log, errors);
                setPlainField(form, 'diem', args.diem, 'diem', false, log, errors);

                if (args.xepLoaiVal !== null && args.xepLoaiVal !== undefined && args.xepLoaiVal !== '') {
                    await setComboField(
                        xepLoaiField,
                        args.xepLoaiVal,
                        'xep_loai',
                        {required: false, waitAfterMs: 0},
                        log,
                        errors
                    );
                } else {
                    log.push('xep_loai: skipped (no value)');
                }

                return {
                    ok: errors.length === 0,
                    log: log,
                    errors: errors,
                    formId: formPanel.id || ''
                };
            }''', payload)

            if verified_result.get("ok"):
                log_entries = list(verified_result.get("log", []))
                auto_noi_dung = bool(payload.get("autoNoiDung"))
                if auto_noi_dung:
                    native_ok, native_msg, native_logs = self._native_retype_ppct_and_wait_noi_dung(
                        ppct=str(ppct),
                        mon_hoc_value=mon_hoc_index,
                        phan_mon_value=phan_mon_index,
                        timeout_s=1.8,
                    )
                    log_entries = [
                        entry for entry in log_entries
                        if "deferred_native_" not in entry
                    ]
                    log_entries.extend(native_logs)
                    if not native_ok:
                        logger.warning(
                            "Native PPCT autofill failed: %s | log=%s",
                            native_msg,
                            log_entries,
                        )
                        return False, f"Lỗi nhập form: {native_msg}"

                logger.info(f"Form filled: {', '.join(log_entries)}")
                return True, f"Đã nhập: {', '.join(log_entries)}"

            verify_errors = verified_result.get("errors", [])
            verify_logs = verified_result.get("log", [])
            logger.warning(
                f"Verified form fill failed: {verify_errors}, log: {verify_logs}"
            )
            return False, f"Lỗi nhập form: {'; '.join(verify_errors)}"

        except PlaywrightTimeout:
            return False, "Timeout nhập form"
        except Exception as e:
            return False, f"Lỗi nhập form: {type(e).__name__}: {str(e)[:80]}"

    def save_form(self):
        """Click nút Lưu trên ExtJS form popup + xử lý dialog xác nhận.

        VnEdu v5 dùng ExtJS button — tìm qua Ext.ComponentQuery hoặc DOM.
        Sau click: chờ dialog confirm → auto accept → chờ form đóng.

        Returns:
            (success: bool, message: str)
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        try:
            # Đăng ký dialog handler TRƯỚC khi click Lưu
            self._handle_dialogs()

            result = self.page.evaluate('''() => {
                // === Pre-save: Kiểm tra form validation trước khi click Lưu ===
                if (typeof Ext !== 'undefined' && Ext.ComponentQuery) {
                    try {
                        var chkWins = Ext.ComponentQuery.query('window');
                        for (var ci = 0; ci < chkWins.length; ci++) {
                            var cw = chkWins[ci];
                            if (!cw.isVisible || !cw.isVisible()) continue;
                            var ct = (cw.title || '').toLowerCase();
                            if (ct.indexOf('chi ti') < 0 && ct.indexOf('ti\\u1EBFt h\\u1ECDc') < 0) continue;
                            var cfp = cw.down('form');
                            if (cfp && cfp.getForm) {
                                var cForm = cfp.getForm();
                                if (!cForm.isValid()) {
                                    var invalids = [];
                                    try {
                                        cForm.getFields().each(function(ff) {
                                            if (ff.isValid && !ff.isValid()) {
                                                var errLabel = ff.fieldLabel || ff.getName() || '?';
                                                var errMsgs = ff.getErrors ? ff.getErrors().join(', ') : 'invalid';
                                                invalids.push(errLabel + ': ' + errMsgs);
                                            }
                                        });
                                    } catch(efv) {}
                                    return {
                                        ok: false,
                                        error: 'Validation: ' + invalids.join('; '),
                                        validation: true,
                                        invalidFields: invalids
                                    };
                                }
                            }
                            break;
                        }
                    } catch(evc) {}
                }

                // === Chiến lược 1: Tìm ExtJS button "Lưu" trong window popup ===
                if (typeof Ext !== 'undefined' && Ext.ComponentQuery) {
                    try {
                        // Tìm window popup "chi tiết tiết học"
                        const wins = Ext.ComponentQuery.query('window');
                        for (const w of wins) {
                            if (!w.isVisible || !w.isVisible()) continue;
                            const title = w.title || '';
                            if (title.indexOf('chi ti') < 0 && title.indexOf('ti\\u1EBFt h\\u1ECDc') < 0) continue;

                            // Tìm button "Lưu" trong window
                            const btns = w.query('button');
                            for (const btn of btns) {
                                const text = (btn.text || '').trim();
                                if (text === 'L\\u01B0u' || text === 'Luu' || text.toLowerCase() === 'save') {
                                    // Click qua ExtJS handler
                                    if (btn.handler) {
                                        btn.handler.call(btn.scope || btn, btn);
                                    } else if (btn.el && btn.el.dom) {
                                        btn.el.dom.click();
                                    }
                                    return {
                                        ok: true,
                                        method: 'extjs_handler',
                                        btnText: text,
                                        btnId: btn.id || ''
                                    };
                                }
                            }
                        }
                    } catch(e) {
                        // ExtJS error → fallback
                    }
                }

                // === Chiến lược 2: Fallback — tìm DOM button "Lưu" ===
                const saveKeywords = ['l\\u01B0u', 'luu', 'save'];
                let saveBtn = null;

                for (const btn of document.querySelectorAll('button, input[type="button"], input[type="submit"]')) {
                    if (btn.offsetParent === null) continue;
                    const text = (btn.value || btn.innerText || '').toLowerCase().trim();
                    for (const kw of saveKeywords) {
                        if (text.includes(kw)) {
                            saveBtn = btn;
                            break;
                        }
                    }
                    if (saveBtn) break;
                }

                if (!saveBtn) {
                    return {ok: false, error: 'Khong tim thay nut Luu'};
                }

                saveBtn.click();
                return {
                    ok: true,
                    method: 'dom_click',
                    btnText: (saveBtn.value || saveBtn.innerText || '').trim().substring(0, 20),
                    btnId: saveBtn.id || ''
                };
            }''')

            if not result.get("ok"):
                err_msg = result.get("error", "Unknown")
                # Validation error: trả về chi tiết fields lỗi
                if result.get("validation"):
                    invalid_fields = result.get("invalidFields", [])
                    logger.warning(f"Save blocked — form validation failed: {invalid_fields}")
                    return False, f"Validation lỗi: {err_msg}"
                return False, err_msg

            method = result.get("method", "?")
            btn_text = result.get("btnText", "?")
            logger.info(f"Save clicked: method={method}, btn={btn_text}")

            wait_result = self.page.evaluate('''async (args) => {
                const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

                function isLessonTitle(title) {
                    const t = String(title || '').toLowerCase();
                    return (
                        t.indexOf('chi ti') >= 0 ||
                        t.indexOf('ti\\u1EBFt h\\u1ECDc') >= 0 ||
                        t.indexOf('s\\u1ED5 \\u0111\\u1EA7u b\\u00E0i') >= 0 ||
                        t.indexOf('so dau bai') >= 0
                    );
                }

                function collectWindows() {
                    const wins = [];
                    if (typeof Ext === 'undefined' || !Ext.ComponentQuery) {
                        return wins;
                    }
                    const extWins = Ext.ComponentQuery.query('window');
                    for (const w of extWins) {
                        try {
                            if (!(w.isVisible && w.isVisible())) continue;
                            wins.push({
                                title: String(w.title || ''),
                                lesson: isLessonTitle(w.title || ''),
                                xtype: String(w.xtype || ''),
                            });
                        } catch (e) {}
                    }
                    return wins;
                }

                function clickMessageOk() {
                    const actions = [];
                    if (typeof Ext === 'undefined' || !Ext.ComponentQuery) {
                        return actions;
                    }

                    try {
                        if (Ext.Msg && Ext.Msg.isVisible && Ext.Msg.isVisible()) {
                            const btns = Ext.Msg.query('button');
                            for (const btn of btns) {
                                const txt = String(btn.text || '').trim();
                                if (txt === 'OK' || txt === 'Yes') {
                                    if (btn.el && btn.el.dom) btn.el.dom.click();
                                    actions.push('ExtMsg:' + txt);
                                    return actions;
                                }
                            }
                            if (btns.length > 0 && btns[0].el && btns[0].el.dom) {
                                btns[0].el.dom.click();
                                actions.push('ExtMsg:first');
                                return actions;
                            }
                        }
                    } catch (e1) {}

                    try {
                        const wins = Ext.ComponentQuery.query('window');
                        for (const w of wins) {
                            if (!(w.isVisible && w.isVisible())) continue;
                            const title = String(w.title || '');
                            const tLow = title.toLowerCase();
                            if (isLessonTitle(title)) continue;
                            if (
                                tLow.indexOf('th\\u00f4ng b\\u00e1o') >= 0 ||
                                tLow.indexOf('thong bao') >= 0 ||
                                tLow.indexOf('notification') >= 0 ||
                                tLow.indexOf('success') >= 0 ||
                                tLow.indexOf('x\\u00e1c nh\\u1EADn') >= 0 ||
                                tLow.indexOf('confirm') >= 0
                            ) {
                                const btns = w.query('button');
                                for (const btn of btns) {
                                    if (btn.el && btn.el.dom) {
                                        btn.el.dom.click();
                                        actions.push('Window:' + (btn.text || 'button'));
                                        return actions;
                                    }
                                }
                                try {
                                    w.close();
                                    actions.push('Window:close');
                                    return actions;
                                } catch (e2) {}
                            }
                        }
                    } catch (e3) {}

                    return actions;
                }

                let actionLog = [];
                let lastWindows = [];
                const deadline = Date.now() + Math.max(args.timeoutMs || 0, 1800);
                while (Date.now() < deadline) {
                    const clicked = clickMessageOk();
                    if (clicked.length) {
                        actionLog = actionLog.concat(clicked);
                    }

                    const wins = collectWindows();
                    lastWindows = wins;
                    const lessonOpen = wins.some(item => item.lesson);
                    const loadMaskVisible = !!document.querySelector('.x-mask-msg[style*="visible"], .x-mask-loading[style*="visible"]');
                    if (!lessonOpen && !loadMaskVisible) {
                        return {
                            ok: true,
                            actions: actionLog,
                            windows: wins.map(item => item.title),
                        };
                    }
                    await sleep(args.pollMs || 120);
                }

                return {
                    ok: false,
                    actions: actionLog,
                    windows: lastWindows.map(item => item.title),
                };
            }''', {"timeoutMs": 3200, "pollMs": 110})

            if wait_result.get("ok"):
                logger.info(
                    "Form saved + closed successfully "
                    f"(actions={wait_result.get('actions', [])})"
                )
                return True, "Đã lưu thành công"

            closed_ok, close_msg = self.wait_for_lesson_form_closed(
                timeout_s=0.8,
                poll_interval=0.06,
            )
            if closed_ok:
                logger.info(
                    "Form closed shortly after save wait timeout "
                    f"(actions={wait_result.get('actions', [])})"
                )
                return True, "Đã lưu thành công"

            logger.warning(
                "Form vẫn mở sau save wait: "
                f"windows={wait_result.get('windows', [])}, close={close_msg}"
            )
            return False, "Form vẫn mở sau khi lưu (có thể lỗi validation)"

        except PlaywrightTimeout:
            return False, "Timeout lưu form"
        except Exception as e:
            return False, f"Lỗi lưu form: {type(e).__name__}: {str(e)[:80]}"

    def save_form_via_api(self, buoi_hoc=None):
        """Lưu trực tiếp qua service nội bộ của VnEdu trong page context hiện tại.

        Ưu tiên đường API-first để giảm độ trễ popup/dialog. Method này chỉ dùng
        session/cookie đang có trong Chrome, không dùng requests ngoài trình duyệt.

        Args:
            buoi_hoc: str|None — giá trị buổi hiện tại của slot ("Sáng"/"Chiều")

        Returns:
            (success: bool, message: str, meta: dict)
            meta chứa:
                - method: str
                - request_sent: bool
                - server_msg: str
                - used_api: bool
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP", {
                "method": "api_precheck",
                "request_sent": False,
                "used_api": False,
            }

        buoi_text = str(buoi_hoc or "").strip()
        if not buoi_text:
            return False, "Thiếu buổi học cho API save", {
                "method": "api_precheck",
                "request_sent": False,
                "used_api": False,
            }

        buoi_norm = unicodedata.normalize("NFD", buoi_text)
        buoi_norm = "".join(ch for ch in buoi_norm if unicodedata.category(ch) != "Mn")
        buoi_norm = buoi_norm.strip().lower()
        if buoi_norm in {"sang", "1"}:
            buoi_payload = "1"
        elif buoi_norm in {"chieu", "2"}:
            buoi_payload = "2"
        else:
            buoi_payload = buoi_text

        try:
            result = self.page.evaluate(
                '''async (args) => {
                    const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

                    function isLessonTitle(title) {
                        const t = String(title || '').toLowerCase();
                        return (
                            t.indexOf('chi ti') >= 0 ||
                            t.indexOf('ti\\u1EBFt h\\u1ECDc') >= 0
                        );
                    }

                    function getCombo(name) {
                        if (!(window.Ext && Ext.ComponentQuery)) return null;
                        const combos = Ext.ComponentQuery.query('combobox');
                        for (const combo of combos) {
                            try {
                                const comboName = combo.getName ? combo.getName() : (combo.name || '');
                                if (comboName === name) return combo;
                            } catch (e) {}
                        }
                        return null;
                    }

                    function getVisibleLessonWindow() {
                        if (!(window.Ext && Ext.ComponentQuery)) return null;
                        const wins = Ext.ComponentQuery.query('window');
                        for (const w of wins) {
                            try {
                                if (!(w.isVisible && w.isVisible())) continue;
                                if (!isLessonTitle(w.title || '')) continue;
                                const formPanel = w.down ? w.down('form') : null;
                                if (formPanel && formPanel.getForm) return w;
                            } catch (e) {}
                        }
                        return null;
                    }

                    const lessonWin = getVisibleLessonWindow();
                    if (!lessonWin) {
                        return {
                            ok: false,
                            error: 'Không tìm thấy popup chi tiết tiết học',
                            request_sent: false,
                            method: 'api_prepare'
                        };
                    }

                    const formPanel = lessonWin.down ? lessonWin.down('form') : null;
                    if (!formPanel || !formPanel.getForm) {
                        return {
                            ok: false,
                            error: 'Popup không có form hợp lệ',
                            request_sent: false,
                            method: 'api_prepare'
                        };
                    }

                    const form = formPanel.getForm();
                    let stableState = {
                        valid: false,
                        invalidFields: [],
                        loadMaskVisible: false,
                        comboLoading: false,
                    };
                    const stableDeadline = Date.now() + Math.max(parseInt(args.stableWaitMs || 0, 10), 600);
                    while (Date.now() < stableDeadline) {
                        let invalids = [];
                        let loadMaskVisible = false;
                        let comboLoading = false;
                        let valid = false;
                        try {
                            const masks = document.querySelectorAll('.x-mask-msg, .x-mask-loading, .x-mask');
                            for (const mask of masks) {
                                try {
                                    if (!mask) continue;
                                    const style = window.getComputedStyle ? window.getComputedStyle(mask) : null;
                                    if (
                                        style &&
                                        style.display !== 'none' &&
                                        style.visibility !== 'hidden' &&
                                        parseFloat(style.opacity || '1') > 0
                                    ) {
                                        loadMaskVisible = true;
                                        break;
                                    }
                                } catch (eMask) {}
                            }
                        } catch (eMaskAll) {}

                        try {
                            const comboFields = formPanel.query ? formPanel.query('combobox') : [];
                            for (const field of comboFields) {
                                try {
                                    const store = field.getStore ? field.getStore() : field.store;
                                    if (store && store.isLoading && store.isLoading()) {
                                        comboLoading = true;
                                        break;
                                    }
                                } catch (eStore) {}
                            }
                        } catch (eCombo) {}

                        try {
                            valid = !!form.isValid();
                        } catch (eValid) {
                            valid = false;
                        }

                        if (!valid) {
                            try {
                                form.getFields().each(function(field) {
                                    if (field.isValid && !field.isValid()) {
                                        const label = field.fieldLabel || field.getName() || '?';
                                        const errs = field.getErrors ? field.getErrors().join(', ') : 'invalid';
                                        invalids.push(label + ': ' + errs);
                                    }
                                });
                            } catch (eFields) {}
                        }

                        stableState = {
                            valid: valid,
                            invalidFields: invalids,
                            loadMaskVisible: loadMaskVisible,
                            comboLoading: comboLoading,
                        };

                        if (valid && !comboLoading && !loadMaskVisible) {
                            break;
                        }

                        await sleep(args.stablePollMs || 120);
                    }

                    if (!stableState.valid) {
                        const invalids = [];
                        invalids.push.apply(invalids, stableState.invalidFields || []);
                        return {
                            ok: false,
                            error: 'Validation: ' + invalids.join('; '),
                            validation: true,
                            invalidFields: invalids,
                            request_sent: false,
                            method: 'api_prepare',
                            readiness: {
                                loadmask: !!stableState.loadMaskVisible,
                                combo_loading: !!stableState.comboLoading,
                            }
                        };
                    }

                    if (!(window.$ && $.ajax)) {
                        return {
                            ok: false,
                            error: 'jQuery.ajax không sẵn sàng',
                            request_sent: false,
                            method: 'api_prepare'
                        };
                    }

                    const capCombo = getCombo('cboCapHoc');
                    const weekCombo = getCombo('cboTuanHoc');
                    const classCombo = getCombo('cboLopHoc');
                    const capHoc = capCombo && capCombo.getValue ? capCombo.getValue() : '';
                    const tuanHoc = weekCombo && weekCombo.getValue ? weekCombo.getValue() : '';
                    const lopHocId = classCombo && classCombo.getValue ? classCombo.getValue() : '';

                    if (capHoc === '' || tuanHoc === '' || lopHocId === '') {
                        return {
                            ok: false,
                            error: 'Thiếu context lớp/tuần/cấp học cho API save',
                            request_sent: false,
                            method: 'api_prepare',
                            context: {
                                cap_hoc: capHoc,
                                tuan_hoc: tuanHoc,
                                lop_hoc_id: lopHocId,
                            }
                        };
                    }

                    const payload = form.getValues();
                    payload.cap_hoc = capHoc;
                    payload.lop_hoc_id = lopHocId;
                    payload.tuan_hoc = tuanHoc;
                    payload.buoi_hoc = args.buoiHoc;

                    const basePath = (
                        typeof applicationPath !== 'undefined' && applicationPath
                    ) ? String(applicationPath) : './';
                    const url = basePath + '?call=app.sodaubai.serv.so_dau_bai.save';
                    let requestSent = false;
                    let ajaxResult = null;
                    const winId = lessonWin.id || '';

                    try {
                        if (window.Ext && Ext.util && Ext.util.Mask && typeof Ext.util.Mask.show === 'function' && winId) {
                            Ext.util.Mask.show(winId);
                        }
                    } catch (eMaskShow) {}

                    try {
                        ajaxResult = await new Promise((resolve) => {
                            requestSent = true;
                            $.ajax({
                                url: url,
                                type: 'post',
                                data: payload,
                                success: function (rs) {
                                    resolve({
                                        ok: true,
                                        raw: rs,
                                    });
                                },
                                error: function (xhr, ajaxOptions, thrownError) {
                                    resolve({
                                        ok: false,
                                        status: xhr && typeof xhr.status !== 'undefined' ? xhr.status : null,
                                        error: String(thrownError || (xhr && xhr.statusText) || 'ajax_error'),
                                        responseText: xhr && xhr.responseText ? String(xhr.responseText).slice(0, 200) : '',
                                    });
                                }
                            });
                        });
                    } finally {
                        try {
                            if (window.Ext && Ext.util && Ext.util.Mask && typeof Ext.util.Mask.hide === 'function' && winId) {
                                Ext.util.Mask.hide(winId);
                            }
                        } catch (eMaskHide) {}
                    }

                    if (!ajaxResult || !ajaxResult.ok) {
                        return {
                            ok: false,
                            error: ajaxResult && ajaxResult.error ? ajaxResult.error : 'ajax_error',
                            status_code: ajaxResult && ajaxResult.status,
                            response_text: ajaxResult && ajaxResult.responseText,
                            request_sent: requestSent,
                            method: 'api_ajax'
                        };
                    }

                    let data = ajaxResult.raw;
                    if (typeof data === 'string') {
                        try {
                            data = JSON.parse(data);
                        } catch (eJson) {
                            return {
                                ok: false,
                                error: 'JSON parse lỗi: ' + String(eJson && eJson.message ? eJson.message : eJson),
                                raw: String(ajaxResult.raw).slice(0, 200),
                                request_sent: requestSent,
                                method: 'api_parse'
                            };
                        }
                    }

                    if (!data || !data.success) {
                        return {
                            ok: false,
                            error: data && data.msg ? String(data.msg) : 'Server báo lưu thất bại',
                            payload: data || null,
                            request_sent: requestSent,
                            method: 'api_response'
                        };
                    }

                    // Không tự click Refresh sau từng tiết. Worker xác minh bằng
                    // service fetch; refresh UI liên tục làm trang load lặp và có
                    // thể để lại mask/combobox ở trạng thái khó thao tác.
                    let refreshClicked = false;

                    let popupClosed = false;
                    try {
                        lessonWin.close();
                        popupClosed = true;
                    } catch (eClose) {
                        try {
                            if (lessonWin.hide) {
                                lessonWin.hide();
                                popupClosed = true;
                            }
                        } catch (eHide) {}
                    }

                    return {
                        ok: true,
                        method: 'api_ajax',
                        request_sent: requestSent,
                        refresh_clicked: refreshClicked,
                        popup_closed: popupClosed,
                        server_msg: data && data.msg ? String(data.msg) : '',
                    };
                }''',
                {"buoiHoc": buoi_payload, "stableWaitMs": 1800, "stablePollMs": 120},
            )

            if result.get("ok"):
                closed_ok, close_msg = self.wait_for_lesson_form_closed(
                    timeout_s=0.8,
                    poll_interval=0.06,
                )
                logger.info(
                    "API save success: "
                    f"refresh={result.get('refresh_clicked')}, "
                    f"popup_closed={result.get('popup_closed')}, "
                    f"close_wait={closed_ok}:{close_msg}"
                )
                return True, "Đã lưu thành công", {
                    "method": result.get("method", "api_ajax"),
                    "request_sent": bool(result.get("request_sent", False)),
                    "server_msg": result.get("server_msg", ""),
                    "used_api": True,
                }

            err_msg = result.get("error", "API save thất bại")
            if result.get("validation"):
                invalid_fields = result.get("invalidFields", [])
                logger.warning(f"API save blocked — form validation failed: {invalid_fields}")
                return False, f"Validation lỗi: {err_msg}", {
                    "method": result.get("method", "api_prepare"),
                    "request_sent": bool(result.get("request_sent", False)),
                    "server_msg": "",
                    "used_api": True,
                    "validation": True,
                }

            logger.warning(
                "API save failed: "
                f"method={result.get('method')}, "
                f"request_sent={result.get('request_sent')}, "
                f"error={err_msg}"
            )
            return False, err_msg, {
                "method": result.get("method", "api_unknown"),
                "request_sent": bool(result.get("request_sent", False)),
                "server_msg": result.get("error", ""),
                "used_api": True,
            }

        except PlaywrightTimeout:
            return False, "Timeout lưu form qua API", {
                "method": "api_timeout",
                "request_sent": False,
                "used_api": True,
            }
        except Exception as e:
            return False, f"Lỗi lưu form qua API: {type(e).__name__}: {str(e)[:80]}", {
                "method": "api_exception",
                "request_sent": False,
                "used_api": True,
            }

    def save_form_auto(self, buoi_hoc=None):
        """Ưu tiên API-first save; chỉ fallback click-save nếu request chưa hề gửi."""
        api_ok, api_msg, api_meta = self.save_form_via_api(buoi_hoc=buoi_hoc)
        if api_ok:
            return True, api_msg, {
                "method": api_meta.get("method", "api_ajax"),
                "used_api": True,
                "request_sent": bool(api_meta.get("request_sent", False)),
                "fallback_used": False,
            }

        if api_meta.get("validation"):
            return False, api_msg, {
                "method": api_meta.get("method", "api_validation"),
                "used_api": True,
                "request_sent": False,
                "fallback_used": False,
            }

        request_sent = bool(api_meta.get("request_sent", False))
        if request_sent:
            return False, api_msg, {
                "method": api_meta.get("method", "api_failed"),
                "used_api": True,
                "request_sent": True,
                "fallback_used": False,
            }

        logger.info(
            "API save skipped/fallback to legacy click-save: "
            f"{api_msg} ({api_meta.get('method', 'api_precheck')})"
        )
        legacy_ok, legacy_msg = self.save_form()
        return legacy_ok, legacy_msg, {
            "method": "legacy_click_save",
            "used_api": False,
            "request_sent": False,
            "fallback_used": True,
            "api_message": api_msg,
        }

    def _handle_dialogs(self):
        """Đảm bảo dialog auto-accept đã được đăng ký (idempotent).

        VnEdu thường hiện confirm "Bạn có chắc chắn muốn lưu?" hoặc alert
        sau khi click Lưu. Phải đăng ký handler TRƯỚC khi click.
        Gọi method này trước mỗi action có thể trigger dialog — nó ủy quyền
        cho setup_dialog_auto_accept để KHÔNG tạo listener chồng nhau.
        """
        self.setup_dialog_auto_accept()

    # -----------------------------------------------------------------
    # 3.6: COMPLETE ENTRY FLOW (Click ➕ → Fill → Save)
    # -----------------------------------------------------------------

    def close_form(self):
        """Đóng ExtJS form popup (click nút Đóng / X) — dùng cho error recovery.

        Đóng BẤT KỲ ExtJS window popup visible nào (chi tiết tiết học,
        ý kiến GVCN, thông báo, v.v.).

        Returns:
            (success: bool, message: str)
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        try:
            result = self.page.evaluate('''() => {
                if (typeof Ext === 'undefined') return {ok: false, error: 'No ExtJS'};

                var closed = [];
                var skipped = [];
                var wins = Ext.ComponentQuery.query('window');
                for (var i = 0; i < wins.length; i++) {
                    var w = wins[i];
                    if (!w.isVisible || !w.isVisible()) continue;

                    // Safety filter: chỉ đóng popup/dialog, KHÔNG đóng content panels
                    var title = (w.title || '').toLowerCase();
                    var isPopup = false;
                    // Known popup patterns
                    if (title.indexOf('chi ti') >= 0 ||
                        title.indexOf('ti\\u1EBFt h\\u1ECDc') >= 0 ||
                        title.indexOf('\\u00fd ki\\u1EBFn') >= 0 ||
                        title.indexOf('y kien') >= 0 ||
                        title.indexOf('gvcn') >= 0 ||
                        title.indexOf('th\\u00f4ng b\\u00e1o') >= 0 ||
                        title.indexOf('thong bao') >= 0 ||
                        title.indexOf('x\\u00e1c nh\\u1EADn') >= 0 ||
                        title.indexOf('notification') >= 0 ||
                        title.indexOf('confirm') >= 0 ||
                        title.indexOf('th\\u00eam') >= 0 ||
                        title.indexOf('s\\u1EEDa') >= 0) {
                        isPopup = true;
                    }
                    // Has a form inside → likely a data entry popup
                    if (!isPopup && w.down && w.down('form')) isPopup = true;
                    // Is a messagebox
                    if (!isPopup && (w.xtype === 'messagebox' ||
                        w.$className === 'Ext.window.MessageBox')) isPopup = true;
                    // Is explicitly modal
                    if (!isPopup && w.modal) isPopup = true;

                    if (!isPopup) {
                        skipped.push(w.title || w.id);
                        continue;
                    }

                    // Thử click nút Đóng/Close
                    var btns = w.query('button');
                    var found = false;
                    for (var j = 0; j < btns.length; j++) {
                        var btn = btns[j];
                        var text = (btn.text || '').trim().toLowerCase();
                        if (text === '\\u0111\\u00f3ng' || text === 'close' ||
                            text === 'dong' || text === 'cancel' ||
                            text === 'h\\u1ee7y' || text === 'ok') {
                            if (btn.handler) {
                                btn.handler.call(btn.scope || btn, btn);
                            } else if (btn.el && btn.el.dom) {
                                btn.el.dom.click();
                            }
                            closed.push(w.title || w.id);
                            found = true;
                            break;
                        }
                    }
                    if (!found) {
                        // Fallback: w.close() trực tiếp
                        try {
                            w.close();
                            closed.push((w.title || w.id) + ' (w.close)');
                        } catch(e) {}
                    }
                }
                return {ok: true, closed: closed, skipped: skipped,
                        method: closed.length > 0 ? 'closed' : 'no_popup'};
            }''')

            closed = result.get("closed", [])
            skipped = result.get("skipped", [])
            if closed:
                logger.info(f"Close form: closed {len(closed)} popup(s): {closed}")
            if skipped:
                logger.debug(f"Close form: skipped {len(skipped)} window(s): {skipped}")
            return True, result.get("method", "ok")

        except Exception as e:
            return False, f"Lỗi đóng form: {type(e).__name__}: {str(e)[:80]}"

    def type_one_entry(self, row_index, ppct, hs_nghi, nhan_xet, diem,
                       phan_mon_index=None, xep_loai=None, noi_dung=None,
                       pre_click_delay=0.3, row_dom_index=None):
        """Nhập liệu hoàn chỉnh cho 1 tiết: click ➕ → fill form → save.

        Args:
            row_index: add_btn_index của row mục tiêu (từ read_table)
            ppct: str — tiết PPCT
            hs_nghi: str — số HS nghỉ
            nhan_xet: str — nhận xét
            diem: str — điểm tiết học
            phan_mon_index: str|int|None — value Phân môn (None = giữ nguyên)
            xep_loai: str|None — value Xếp loại (None = giữ nguyên)
            noi_dung: str|None — tên bài / nội dung (None = "---")
            pre_click_delay: float — delay trước khi click (s)
            row_dom_index: int|None — rowIdx DOM từ read_table(), ưu tiên click
                đúng dấu ➕ nằm trên hàng đã match

        Returns:
            (success: bool, message: str)
        """
        if self.should_stop:
            return False, "Đã yêu cầu dừng"

        row_meta = None
        current_context = {"tuan": "", "lop": ""}
        ok_rows, rows_data = self.read_table()
        if ok_rows:
            for row in rows_data:
                if row_dom_index is not None and row.get("rowIdx") == row_dom_index:
                    row_meta = row
                    break
                if row.get("add_btn_index") == row_index:
                    row_meta = row
                    break
        ok_ctx, ctx_data = self.get_current_selection()
        if ok_ctx and isinstance(ctx_data, dict):
            current_context = ctx_data

        # Bước 0: Delay trước click (nếu caller yêu cầu)
        if pre_click_delay and pre_click_delay > 0:
            time.sleep(pre_click_delay)

        # Bước 1: Click nút ➕
        ok, msg = self.click_add_button(row_index, row_dom_index=row_dom_index)
        if not ok:
            return False, f"Click ➕ thất bại: {msg}"

        if self.should_stop:
            self.close_form()
            return False, "Đã yêu cầu dừng"

        # Bước 2: Chờ form sẵn sàng
        ok, msg = self.wait_for_lesson_form(timeout_s=4.0, poll_interval=0.12)
        if not ok:
            try:
                self.close_form()
            except Exception:
                pass
            return False, f"Form chưa mở sẵn sàng: {msg}"

        # Bước 3: Nhập liệu
        ok, msg = self.fill_form(ppct, hs_nghi, nhan_xet, diem,
                                 phan_mon_index, xep_loai, noi_dung)
        if not ok:
            # Đóng form để recovery
            self.close_form()
            return False, f"Nhập form thất bại: {msg}"

        if self.should_stop:
            self.close_form()
            return False, "Đã yêu cầu dừng"

        # Bước 4: Chờ form ổn định rồi lưu
        self.wait_for_form_ready_to_save(timeout_s=0.8, poll_interval=0.06)
        api_buoi = ""
        if row_meta is not None:
            api_buoi = str(row_meta.get("buoi", "")).strip()
        ok, msg, save_meta = self.save_form_auto(buoi_hoc=api_buoi)
        if not ok:
            if row_meta is not None:
                ok_verify = False
                msg_verify = ""
                _row = None
                match_tuan = re.search(r"\d+", str(current_context.get("tuan", "")))
                tuan_num = int(match_tuan.group()) if match_tuan else None
                lop_text = str(current_context.get("lop", "")).strip()
                if tuan_num and lop_text:
                    ok_verify, msg_verify, _row = self.wait_for_slot_data_fetch(
                        lop_text,
                        tuan_num,
                        row_meta.get("thu", ""),
                        row_meta.get("buoi", ""),
                        row_meta.get("tiet", ""),
                        timeout_s=4.5,
                        poll_interval=0.3,
                    )
                if not ok_verify:
                    ok_verify, msg_verify, _row = self.wait_for_slot_data(
                        row_meta.get("thu", ""),
                        row_meta.get("buoi", ""),
                        row_meta.get("tiet", ""),
                        timeout_s=3.0,
                        poll_interval=0.25,
                    )
                if ok_verify:
                    logger.warning(
                        "Save reported failure nhưng bảng đã cập nhật: "
                        f"{msg} | {msg_verify}"
                    )
                    try:
                        self.close_form()
                        self.wait_for_lesson_form_closed(timeout_s=1.0, poll_interval=0.06)
                    except Exception:
                        pass
                    return True, f"Lưu xác minh từ bảng: {msg_verify}"
            try:
                self.close_form()
                self.wait_for_lesson_form_closed(timeout_s=1.0, poll_interval=0.06)
            except Exception:
                pass
            return False, f"Lưu thất bại: {msg}"

        logger.info(
            "Entry saved via %s%s",
            save_meta.get("method", "?"),
            " (fallback)" if save_meta.get("fallback_used") else "",
        )
        return True, f"Tiết {ppct}: OK"

    # -----------------------------------------------------------------
    # 3.7: PAGE UTILITIES
    # -----------------------------------------------------------------

    @staticmethod
    def _seed_current_subject_mapping(form_data):
        """Bổ sung map cho Môn học đang được chọn sẵn từ options live hiện tại.

        Tránh bỏ sót trường hợp popup mở ra đã đứng ở đúng Môn học cần dùng
        (ví dụ Ngoại ngữ), khi đó combo Phân môn đã có dữ liệu thật nhưng
        không phát sinh change event để vòng scan phụ thuộc ghi map.
        """
        data = dict(form_data or {})
        current_value = str(data.get("current_mon_hoc_value") or "").strip()
        current_text = str(data.get("current_mon_hoc_text") or "").strip()
        current_options = list(data.get("phan_mon", []) or [])
        raw_map = data.get("phan_mon_by_mon_hoc", {}) or {}
        normalized_map = {
            str(key): list(options or [])
            for key, options in raw_map.items()
            if key is not None
        }
        mon_options = list(data.get("mon_hoc", []) or [])

        if not current_value and current_text:
            for option in mon_options:
                if str(option.get("text") or "").strip() == current_text:
                    current_value = str(option.get("value") or "").strip()
                    break

        if current_value and current_options and not normalized_map.get(current_value):
            # Chỉ seed khi XÁC ĐỊNH CHẮC môn hiện tại (qua value hoặc text).
            # KHÔNG đoán theo kiểu "môn duy nhất thiếu map == môn của popup":
            # nếu popup đang ở môn khác, đoán như vậy sẽ gán sai phân môn và
            # khiến worker fill nhầm. Thà để thiếu map (fail-closed) để bị chặn
            # với thông điệp "bấm Quét Form lại" còn hơn ghi sai dữ liệu.
            normalized_map[current_value] = current_options

        data["phan_mon_by_mon_hoc"] = normalized_map
        return data

    def read_form_options(self, row_index=0, max_tries=15):
        """Mở form popup "chi tiết tiết học" → đọc dropdown options → đóng form.

        VnEdu có NHIỀU loại nút ➕ trên trang (ý kiến GVCN, chi tiết tiết học).
        Method này thử click từng nút ➕ bắt đầu từ row_index, kiểm tra popup
        title — nếu đúng form "chi tiết tiết học" thì đọc options, nếu sai
        (ví dụ "ý kiến GVCN") thì đóng và thử nút tiếp theo.

        Args:
            row_index: int — bắt đầu từ nút ➕ nào (default 0)
            max_tries: int — số lần thử tối đa (default 15)

        Returns:
            (success: bool, data: dict|str)
            data khi success: {
                'phan_mon': [{'value': '...', 'text': '...'}, ...],
                'phan_mon_by_mon_hoc': {'mon_hoc_value': [{value, text}, ...], ...},
                'xep_loai': [{'value': '...', 'text': '...'}, ...],
                'mon_hoc': [{'value': '...', 'text': '...'}, ...],
                'mon_hoc_field': str (tên field trên VnEdu),
                'current_mon_hoc_value': str (Môn học đang selected trong popup),
                'current_mon_hoc_text': str (raw text Môn học đang hiển thị trong popup)
            }
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        # Bước 1: Thử click từng nút ➕ cho đến khi mở đúng form
        correct_form_opened = False
        tried = 0

        for btn_idx in range(row_index, row_index + max_tries):
            tried += 1
            try:
                ok, msg = self.click_add_button(btn_idx)
                if not ok:
                    logger.debug(f"read_form_options: btn #{btn_idx} click fail: {msg}")
                    break  # Hết nút → dừng

                popup_check = {"type": "none"}
                for _popup_try in range(10):
                    time.sleep(POST_CLICK_DELAY)
                    popup_check = self.page.evaluate('''() => {
                        if (typeof Ext === 'undefined' || !Ext.ComponentQuery)
                            return {type: 'no_ext'};
                        var wins = Ext.ComponentQuery.query('window');
                        for (var i = 0; i < wins.length; i++) {
                            var w = wins[i];
                            if (!w.isVisible || !w.isVisible()) continue;
                            var title = (w.title || '').toLowerCase();
                            // Form "chi tiết tiết học" — ĐÚNG
                            if (title.indexOf('chi ti') >= 0 &&
                                (title.indexOf('ti\u1EBFt h\u1ECDc') >= 0 ||
                                 title.indexOf('so dau bai') >= 0 ||
                                 title.indexOf('s\u1ED5 \u0111\u1EA7u b\u00E0i') >= 0)) {
                                return {type: 'lesson_detail', title: w.title};
                            }
                            // Form khác (ý kiến GVCN, etc.)
                            if (title.indexOf('ki\u1EBFn') >= 0 ||
                                title.indexOf('gvcn') >= 0 ||
                                title.indexOf('ch\u1EE7 nhi\u1EC7m') >= 0 ||
                                title.indexOf('y kien') >= 0) {
                                return {type: 'gvcn', title: w.title};
                            }
                            // Popup khác có form
                            var f = w.down('form');
                            if (f) {
                                return {type: 'unknown', title: w.title};
                            }
                        }
                        return {type: 'none'};
                    }''')
                    if popup_check.get("type") != "none":
                        break

                popup_type = popup_check.get("type", "none")
                popup_title = popup_check.get("title", "")
                logger.info(
                    f"read_form_options: btn #{btn_idx} → popup "
                    f"type={popup_type}, title={popup_title}"
                )

                if popup_type == "lesson_detail":
                    correct_form_opened = True
                    break
                elif popup_type in ("gvcn", "unknown"):
                    # Sai form → đóng và thử nút tiếp theo
                    logger.info(f"Wrong popup '{popup_title}' → closing, trying next btn")
                    self.close_form()
                    time.sleep(0.12)
                    continue
                elif popup_type == "none":
                    # Không có popup → có thể click chưa hoạt động, thử tiếp
                    time.sleep(0.10)
                    continue
                else:
                    # no_ext
                    return False, "ExtJS not available"

            except Exception as e_try:
                logger.debug(f"read_form_options: btn #{btn_idx} exception: {e_try}")
                try:
                    self.close_form()
                except Exception:
                    pass
                time.sleep(0.12)
                continue

        if not correct_form_opened:
            return False, (
                f"Không tìm thấy form 'chi tiết tiết học' sau {tried} nút ➕. "
                f"Có thể trang chưa load đúng."
            )

        # Bước 2: Đọc combobox options từ ExtJS store
        try:
            verified_result = self.page.evaluate('''async () => {
                const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

                function normalize(value) {
                    try {
                        return String(value == null ? '' : value)
                            .normalize('NFD')
                            .replace(/[\\u0300-\\u036f]/g, '')
                            .trim()
                            .toLowerCase();
                    } catch (e) {
                        return String(value == null ? '' : value).trim().toLowerCase();
                    }
                }

                function findPopupFormPanel() {
                    let formPanel = null;
                    const wins = Ext.ComponentQuery.query('window');
                    for (const w of wins) {
                        if (!(w.isVisible && w.isVisible())) continue;
                        const title = (w.title || '').toLowerCase();
                        if (title.indexOf('chi ti') >= 0 ||
                            title.indexOf('ti\\u1EBFt h\\u1ECDc') >= 0) {
                            const f = w.down('form');
                            if (f) {
                                formPanel = f;
                                break;
                            }
                        }
                    }
                    return formPanel;
                }

                function getFieldStore(field) {
                    if (!field) return null;
                    try {
                        if (field.getStore && typeof field.getStore === 'function') {
                            return field.getStore();
                        }
                    } catch (e) {}
                    try {
                        return field.store || null;
                    } catch (e2) {
                        return null;
                    }
                }

                function getStoreCount(store) {
                    if (!store) return 0;
                    try { return store.getCount ? store.getCount() : 0; } catch (e) { return 0; }
                }

                function getFieldName(field) {
                    if (!field) return '';
                    try { return field.getName ? field.getName() : (field.name || ''); } catch (e) {}
                    return field.name || '';
                }

                function getComboOptions(field) {
                    const options = [];
                    if (!field) return options;
                    const store = getFieldStore(field);
                    if (!store) return options;

                    const valueField = field.valueField || 'id';
                    const displayField = field.displayField || 'name';
                    try {
                        store.each(function(record) {
                            const text = (
                                record.get(displayField) != null
                                    ? record.get(displayField)
                                    : (record.get('name') || record.get('ten') || '')
                            );
                            options.push({
                                value: String(record.get(valueField) != null ? record.get(valueField) : ''),
                                text: String(text != null ? text : '')
                            });
                        });
                    } catch (e) {}
                    return options;
                }

                function findFieldByCandidates(form, candidates, labelKeywords) {
                    const tried = candidates || [];
                    for (let i = 0; i < tried.length; i++) {
                        try {
                            const direct = form.findField(tried[i]);
                            if (direct) return direct;
                        } catch (e) {}
                    }

                    try {
                        const fields = form.getFields().items || [];
                        for (let i = 0; i < fields.length; i++) {
                            const field = fields[i];
                            const label = normalize(field.fieldLabel || field.boxLabel || '');
                            for (let j = 0; j < (labelKeywords || []).length; j++) {
                                if (label.indexOf(labelKeywords[j]) >= 0) {
                                    return field;
                                }
                            }
                        }
                    } catch (e2) {}
                    return null;
                }

                function findRecordInStore(store, value, field) {
                    if (!store) return null;
                    const valueField = field.valueField || 'id';
                    let record = null;
                    try {
                        if (store.findRecord) record = store.findRecord(valueField, value);
                    } catch (e) {}
                    if (!record) {
                        const intValue = parseInt(value, 10);
                        if (!isNaN(intValue)) {
                            try {
                                if (store.findRecord) record = store.findRecord(valueField, intValue);
                            } catch (e2) {}
                        }
                    }
                    return record;
                }

                async function ensureComboStoreLoaded(field, timeoutMs) {
                    if (!field) return {ok: false, count: 0};
                    const start = Date.now();
                    while ((Date.now() - start) < timeoutMs) {
                        const store = getFieldStore(field);
                        const count = getStoreCount(store);
                        if (count > 0) {
                            try { if (field.isExpanded) field.collapse(); } catch (e) {}
                            return {ok: true, count: count};
                        }

                        const fieldName = getFieldName(field);
                        try {
                            const allCombos = Ext.ComponentQuery.query('combobox');
                            for (let i = 0; i < allCombos.length; i++) {
                                const srcCombo = allCombos[i];
                                if (srcCombo === field) continue;
                                let srcName = '';
                                try {
                                    srcName = srcCombo.getName ? srcCombo.getName() : (srcCombo.name || '');
                                } catch (eName) {}
                                if (!fieldName || srcName !== fieldName) continue;

                                const srcStore = getFieldStore(srcCombo);
                                const srcCount = getStoreCount(srcStore);
                                if (!srcStore || srcCount <= 0) continue;

                                try {
                                    field.bindStore(srcStore, true);
                                    const reboundStore = getFieldStore(field);
                                    if (getStoreCount(reboundStore) > 0) {
                                        return {ok: true, count: getStoreCount(reboundStore)};
                                    }
                                } catch (bindErr) {}
                            }
                        } catch (allErr) {}

                        try { if (field.expand) field.expand(); } catch (expandErr) {}
                        await sleep(80);
                    }

                    const available = getComboOptions(field);
                    return {ok: available.length > 0, count: available.length};
                }

                async function setComboValue(field, value) {
                    if (!field) return false;
                    const storeInfo = await ensureComboStoreLoaded(field, 2200);
                    if (!storeInfo.ok) return false;

                    const store = getFieldStore(field);
                    const record = findRecordInStore(store, value, field);
                    if (!record) return false;

                    const valueField = field.valueField || 'id';
                    field.setValue(record.get(valueField));
                    try { field.lastSelection = [record]; } catch (eLast) {}
                    try { field.fireEvent('select', field, [record]); } catch (eSelect) {}
                    try { field.validate(); } catch (eValidate) {}
                    return true;
                }

                function buildOptionsSignature(options) {
                    return (options || [])
                        .slice(0, 30)
                        .map(opt => String(opt.value || '') + '|' + String(opt.text || ''))
                        .join('||');
                }

                async function waitForDependentCombo(parentField, childField, expectedParentValue, timeoutMs, previousSignature) {
                    if (!parentField || !childField) return {ok: false, count: 0};
                    let lastSignature = '';
                    let stableCount = 0;
                    let sawLoading = false;
                    const start = Date.now();
                    const baselineSignature = String(previousSignature || '');

                    while ((Date.now() - start) < timeoutMs) {
                        await ensureComboStoreLoaded(childField, 400);

                        let parentValue = '';
                        try { parentValue = String(parentField.getValue() || ''); } catch (e) {}
                        const options = getComboOptions(childField);
                        let loading = false;
                        try {
                            const childStore = getFieldStore(childField);
                            loading = !!(childStore && childStore.isLoading && childStore.isLoading());
                        } catch (e2) {}
                        if (loading) {
                            sawLoading = true;
                        }

                        const signature = buildOptionsSignature(options);

                        if (parentValue === String(expectedParentValue) && !loading) {
                            stableCount = (signature && signature === lastSignature)
                                ? stableCount + 1
                                : 1;
                            if (
                                stableCount >= 2 &&
                                (
                                    signature !== baselineSignature ||
                                    sawLoading ||
                                    !baselineSignature
                                )
                            ) {
                                return {
                                    ok: true,
                                    count: options.length,
                                    signature: signature,
                                    changed: signature !== baselineSignature,
                                    sawLoading: sawLoading,
                                };
                            }
                        } else {
                            stableCount = 0;
                        }

                        lastSignature = signature;
                        await sleep(80);
                    }

                    const currentOptions = getComboOptions(childField);
                    return {
                        ok: false,
                        count: currentOptions.length,
                        signature: buildOptionsSignature(currentOptions),
                        changed: buildOptionsSignature(currentOptions) !== baselineSignature,
                        sawLoading: sawLoading,
                    };
                }

                if (typeof Ext === 'undefined' || !Ext.ComponentQuery) {
                    return {ok: false, error: 'ExtJS not available'};
                }

                const formPanel = findPopupFormPanel();
                if (!formPanel || !formPanel.getForm) {
                    return {ok: false, error: 'Form panel not found in popup'};
                }

                const form = formPanel.getForm();
                const data = {
                    phan_mon: [],
                    phan_mon_by_mon_hoc: {},
                    xep_loai: [],
                    mon_hoc: [],
                    current_mon_hoc_value: '',
                    current_mon_hoc_text: ''
                };

                const monHocNames = [
                    'mon_hoc_id', 'mon_hoc', 'monhoc_id', 'monhoc',
                    'subject_id', 'ma_mon_hoc'
                ];
                const monField = findFieldByCandidates(form, monHocNames, ['mon hoc']);
                const phanField = findFieldByCandidates(form, ['phan_mon_id'], ['phan mon']);
                const xepLoaiField = findFieldByCandidates(form, ['xep_loai'], ['xep loai']);

                if (monField) {
                    await ensureComboStoreLoaded(monField, 1200);
                    data.mon_hoc = getComboOptions(monField);
                    data.mon_hoc_field = getFieldName(monField);
                    try { data.current_mon_hoc_value = String(monField.getValue() || ''); } catch (e) {}
                    try {
                        data.current_mon_hoc_text = String(
                            (monField.getRawValue ? monField.getRawValue() : monField.rawValue) || ''
                        );
                    } catch (e) {}
                }

                if (xepLoaiField) {
                    await ensureComboStoreLoaded(xepLoaiField, 900);
                    data.xep_loai = getComboOptions(xepLoaiField);
                }

                if (phanField) {
                    await ensureComboStoreLoaded(phanField, 900);
                    data.phan_mon = getComboOptions(phanField);
                }

                if (monField && phanField && data.mon_hoc.length > 0) {
                    let originalMonValue = '';
                    try { originalMonValue = String(monField.getValue() || ''); } catch (e) {}

                    for (let i = 0; i < data.mon_hoc.length; i++) {
                        const monOpt = data.mon_hoc[i];
                        const previousSignature = buildOptionsSignature(getComboOptions(phanField));
                        const setOk = await setComboValue(monField, monOpt.value);
                        if (!setOk) continue;

                        const waitInfo = await waitForDependentCombo(
                            monField,
                            phanField,
                            monOpt.value,
                            1200,
                            previousSignature
                        );
                        if (!waitInfo.ok) {
                            continue;
                        }
                        data.phan_mon_by_mon_hoc[String(monOpt.value)] = getComboOptions(phanField);
                    }

                    if (originalMonValue) {
                        const previousSignature = buildOptionsSignature(getComboOptions(phanField));
                        const restored = await setComboValue(monField, originalMonValue);
                        if (restored) {
                            const restoredWait = await waitForDependentCombo(
                                monField,
                                phanField,
                                originalMonValue,
                                900,
                                previousSignature
                            );
                            const restoredOptions = getComboOptions(phanField);
                            if (restoredWait.ok && restoredOptions.length > 0) {
                                data.phan_mon = restoredOptions;
                            }
                        }
                    }
                }

                return {ok: true, data: data};
            }''')

            # Bước 3: Đóng form
            self.close_form()

            if verified_result.get("ok"):
                data = self._seed_current_subject_mapping(verified_result.get("data", {}))
                pm_count = len(data.get("phan_mon", []))
                xl_count = len(data.get("xep_loai", []))
                mh_count = len(data.get("mon_hoc", []))
                pm_map_count = len(data.get("phan_mon_by_mon_hoc", {}))
                mh_field = data.get("mon_hoc_field", "?")
                logger.info(
                    f"Form options: phan_mon={pm_count}, xep_loai={xl_count}, "
                    f"mon_hoc={mh_count} (field={mh_field}, mapped={pm_map_count})"
                )
                return True, data

            return False, verified_result.get("error", "Unknown error")

        except Exception as e:
            # Cố đóng form nếu còn mở
            try:
                self.close_form()
            except Exception:
                pass
            return False, f"Exception: {type(e).__name__}: {str(e)[:80]}"

    def _wait_page_update(self, timeout_s=15, dropdown_label=None, target_text=None):
        """Chờ page cập nhật sau khi thay đổi dropdown.

        Ưu tiên wait theo tín hiệu thật: network idle, loadmask biến mất,
        dropdown phản ánh giá trị mới và state ổn định.
        """
        try:
            self.page.wait_for_load_state("networkidle", timeout=int(timeout_s * 1000))
        except PlaywrightTimeout:
            logger.debug("networkidle timeout — falling back to DOM/update polling")
            try:
                self.page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass

        deadline = time.time() + max(timeout_s, 0.5)
        stable_hits = 0
        last_state = {}
        while time.time() < deadline:
            try:
                state = self.page.evaluate('''(args) => {
                    const label = String(args.label || '');
                    const targetText = String(args.targetText || '').trim().toLowerCase();

                    function normalize(text) {
                        return String(text == null ? '' : text).trim().toLowerCase();
                    }

                    function digitsOnly(text) {
                        return String(text == null ? '' : text).replace(/\\D/g, '');
                    }

                    function textMatches(currentText) {
                        if (!targetText) return true;
                        const current = normalize(currentText);
                        if (!current) return false;
                        if (current === targetText || current.indexOf(targetText) >= 0) return true;
                        const currentNum = digitsOnly(current);
                        const targetNum = digitsOnly(targetText);
                        return !!(currentNum && targetNum && currentNum === targetNum);
                    }

                    let ajaxBusy = false;
                    try {
                        ajaxBusy = !!(
                            typeof Ext !== 'undefined' &&
                            Ext.Ajax &&
                            Ext.Ajax.isLoading &&
                            Ext.Ajax.isLoading()
                        );
                    } catch (e) {}

                    let loadMaskVisible = false;
                    try {
                        const masks = document.querySelectorAll('.x-mask-msg, .x-mask-loading, .x-mask');
                        for (const mask of masks) {
                            if (mask.offsetParent !== null) {
                                loadMaskVisible = true;
                                break;
                            }
                        }
                    } catch (e2) {}

                    let selectedText = '';
                    if (label) {
                        const nameMap = {
                            'tuan': 'cboTuanHoc',
                            'lop': 'cboLopHoc',
                            'cap': 'cboCapHoc'
                        };
                        const comboName = nameMap[label];
                        if (comboName && typeof Ext !== 'undefined' && Ext.ComponentQuery) {
                            try {
                                const combos = Ext.ComponentQuery.query('combobox');
                                for (const combo of combos) {
                                    const name = combo.getName ? combo.getName() : (combo.name || '');
                                    if (name !== comboName) continue;
                                    selectedText = combo.getRawValue
                                        ? String(combo.getRawValue() || '')
                                        : '';
                                    break;
                                }
                            } catch (e3) {}
                        }

                        if (!selectedText) {
                            const keywords = {
                                'tuan': ['tuan', 'cboTuanHoc'],
                                'lop': ['lop', 'cboLopHoc'],
                                'cap': ['cap', 'cboCapHoc'],
                            };
                            const kws = keywords[label] || [label];
                            for (const sel of document.querySelectorAll('select')) {
                                const id = normalize(sel.id || '');
                                const name = normalize(sel.name || '');
                                let matched = false;
                                for (const kw of kws) {
                                    if (id.indexOf(normalize(kw)) >= 0 || name.indexOf(normalize(kw)) >= 0) {
                                        matched = true;
                                        break;
                                    }
                                }
                                if (!matched) continue;
                                const idx = sel.selectedIndex;
                                if (idx >= 0 && sel.options[idx]) {
                                    selectedText = String(sel.options[idx].text || '');
                                }
                                break;
                            }
                        }
                    }

                    return {
                        ready: document.readyState === 'complete',
                        ajaxBusy: ajaxBusy,
                        loadMaskVisible: loadMaskVisible,
                        selectedText: selectedText,
                        selectedOk: textMatches(selectedText),
                    };
                }''', {"label": dropdown_label, "targetText": target_text})
            except Exception as e:
                last_state = {"error": f"{type(e).__name__}: {str(e)[:80]}"}
                time.sleep(0.08)
                continue

            last_state = state or {}
            ready_now = (
                last_state.get("ready")
                and not last_state.get("ajaxBusy")
                and not last_state.get("loadMaskVisible")
                and last_state.get("selectedOk", True)
            )
            if ready_now:
                stable_hits += 1
                if stable_hits >= 2:
                    return True, last_state
            else:
                stable_hits = 0
            time.sleep(0.08)

        return False, last_state

    def inspect_page(self):
        """Dump cấu trúc trang VnEdu để debug.

        Trả về thông tin: dropdowns (ExtJS + HTML select), tables,
        buttons, forms, add buttons.
        Dùng khi cần tìm selectors chính xác.

        Returns:
            (success, dict)
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        try:
            result = self.page.evaluate('''() => {
                const info = {
                    url: location.href,
                    title: document.title,
                    selects: [],
                    extCombos: [],
                    tables: [],
                    forms: [],
                    buttons: [],
                    addButtons: 0,
                };

                // === ExtJS Comboboxes (VnEdu v5 dùng ExtJS 4.x) ===
                if (typeof Ext !== 'undefined' && Ext.ComponentQuery) {
                    try {
                        const combos = Ext.ComponentQuery.query('combobox');
                        for (const c of combos) {
                            let name = '';
                            try { name = c.getName ? c.getName() : (c.name || ''); } catch(e) {}
                            let storeCount = 0;
                            try {
                                const store = c.store;
                                storeCount = (store && store.data && store.data.items)
                                    ? store.data.items.length : 0;
                            } catch(e) {}
                            const hidden = c.isHidden ? c.isHidden() : false;
                            if (hidden) continue;
                            info.extCombos.push({
                                id: c.id || '',
                                name: name,
                                value: String(c.getValue ? c.getValue() : ''),
                                rawValue: c.getRawValue ? c.getRawValue() : '',
                                storeCount: storeCount
                            });
                        }
                    } catch(e) {}
                }

                // === HTML <select> (fallback) ===
                for (const sel of document.querySelectorAll('select')) {
                    info.selects.push({
                        id: sel.id,
                        name: sel.name,
                        optionCount: sel.options.length,
                        currentText: sel.options[sel.selectedIndex]?.text?.trim() || '',
                        visible: sel.offsetParent !== null
                    });
                }

                // === Tables ===
                for (const t of document.querySelectorAll('table')) {
                    const rows = t.querySelectorAll('tr');
                    info.tables.push({
                        id: t.id,
                        className: (t.className || '').substring(0, 50),
                        rows: rows.length,
                        hasRowspan: t.querySelector('td[rowspan]') !== null
                    });
                }

                // === Add buttons (a.add_tiet_so_dau_bai) ===
                info.addButtons = document.querySelectorAll('a.add_tiet_so_dau_bai').length;

                // === Forms ===
                for (const f of document.querySelectorAll('form')) {
                    info.forms.push({
                        id: f.id,
                        action: f.action?.substring(0, 80) || '',
                        method: f.method,
                        inputCount: f.querySelectorAll('input').length
                    });
                }

                // === Visible buttons ===
                for (const btn of document.querySelectorAll(
                    'input[type="button"], input[type="submit"], button'
                )) {
                    if (btn.offsetParent === null) continue;
                    info.buttons.push({
                        tag: btn.tagName,
                        id: btn.id,
                        text: (btn.value || btn.innerText || '').trim().substring(0, 30),
                    });
                }

                return info;
            }''')

            return True, result

        except Exception as e:
            return False, f"Lỗi inspect: {type(e).__name__}: {str(e)[:80]}"

    def setup_dialog_auto_accept(self):
        """Đăng ký auto-accept cho tất cả alert/confirm dialogs (idempotent).

        Chỉ giữ DUY NHẤT một listener: gỡ handler cũ (nếu có) trước khi gắn
        mới, tránh tình trạng nhiều listener cùng accept() một dialog gây lỗi
        "dialog already handled". An toàn gọi nhiều lần.
        """
        if not self.is_connected:
            return

        try:
            def on_dialog(dialog):
                try:
                    logger.info(f"Dialog: {dialog.type} — {dialog.message[:80]}")
                    dialog.accept()
                except Exception as e_accept:
                    # Dialog có thể đã được xử lý/đóng — không để văng lỗi.
                    logger.debug(f"Dialog accept ignored: {e_accept}")

            # Gỡ handler cũ nếu đã đăng ký trước đó
            try:
                if getattr(self, "_dialog_handler", None):
                    self.page.remove_listener("dialog", self._dialog_handler)
            except Exception:
                pass

            self._dialog_handler = on_dialog
            self.page.on("dialog", on_dialog)
            logger.info("Dialog auto-accept enabled")
        except Exception as e:
            logger.debug(f"Dialog setup error: {e}")

    def reload_page(self):
        """Reload trang VnEdu.

        Returns:
            (success: bool, message: str)
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"
        try:
            self.page.reload(wait_until="networkidle", timeout=NAV_TIMEOUT_MS)
            time.sleep(1)
            return True, "Đã reload"
        except Exception as e:
            return False, f"Lỗi reload: {str(e)[:80]}"


_HAS_CDP = HAS_PLAYWRIGHT

# File cấu hình
CONFIG_FILE = "auto_danang_config.json"
CLASS_STATS_CACHE_FILE = "auto_danang_cache.json"
SCHEDULE_RESUME_FILE = "auto_danang_resume.json"
CLASS_STATS_CACHE_TTL_SECONDS = 6 * 3600
CLASS_STATS_CACHE_MAX_ENTRIES = 180
UI_TASK_POLL_MS = 50
CLOSE_GRACE_PERIOD_MS = 3500

# Kích thước cửa sổ
EXPANDED_WIDTH = 585            # Panel trái đủ rộng cho cụm nút CDP không bị chen
RIGHT_PANEL_WIDTH = 505         # Panel phải đủ rộng cho lịch + nút schedule
EXPANDED_TOTAL_WIDTH = EXPANDED_WIDTH + RIGHT_PANEL_WIDTH
EXPANDED_HEIGHT = 680           # Nội dung dài dùng scroll; không kéo cửa sổ xuống taskbar
COMPACT_WIDTH = 430
COMPACT_HEIGHT = 220

# UI tokens: giữ Tkinter/ttk nhưng chuẩn hóa lại hierarchy cho dễ đọc.
UI_BG_APP = "#f6f8fb"
UI_SURFACE = "#ffffff"
UI_SURFACE_ALT = "#f9fafb"
UI_BORDER = "#d1d5db"
UI_TEXT = "#111827"
UI_TEXT_MUTED = "#6b7280"
UI_PRIMARY = "#2563eb"
UI_PRIMARY_ACTIVE = "#1d4ed8"
UI_SUCCESS = "#16a34a"
UI_SUCCESS_ACTIVE = "#15803d"
UI_WARNING = "#d97706"
UI_DANGER = "#dc2626"
UI_DANGER_ACTIVE = "#b91c1c"
UI_DISABLED_BG = "#f3f4f6"
UI_DISABLED_TEXT = "#9ca3af"
UI_LOG_BG = "#111827"
UI_LOG_TEXT = "#d1d5db"

# Lịch dạy — danh sách ngày (Thứ 2→7 + CN)
# thu=8 đại diện cho Chủ nhật. Label hiển thị = "CN".
SCHEDULE_DAYS = [2, 3, 4, 5, 6, 7, 8]
SCHEDULE_DAY_LABELS = {2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "CN"}
SCHEDULE_MODE_MANUAL = "manual"
SCHEDULE_MODE_KHDH = "khdh"


# =====================================================================
# PHẦN 2: CLASS AutoDaNangApp — GIAO DIỆN TKINTER
# =====================================================================

class AutoDaNangApp:
    """GUI Tkinter cho app nhập liệu VnEdu theo Chrome CDP."""

    def __init__(self):
        """Khởi tạo app: state → GUI → load config."""

        # ===== State variables =====

        # UI state
        self.is_compact = False

        # ===== CDP State (Chrome DevTools Protocol) =====
        self._cdp_connected = False
        self._cdp_port = DEFAULT_CDP_PORT

        # ===== Root window =====
        self.root = tk.Tk()
        self.root.title("Sổ đầu bài tự động")
        self.root.configure(bg=UI_BG_APP)
        self.root.resizable(True, True)
        self.style = ttk.Style(self.root)
        self._setup_styles()
        self._init_schedule_checkbox_images()

        # ===== Tkinter variables (phải tạo sau root) =====
        self.var_cdp_port = tk.IntVar(value=DEFAULT_CDP_PORT)

        # ===== Schedule Panel State (Lịch dạy) =====
        self._schedule_thread = None
        self._schedule_queue = queue.Queue()
        self._schedule_running = False
        self._schedule_stop_event = threading.Event()
        self._schedule_stop_reason = None
        self._schedule_results = []
        self._schedule_resume_state = None
        self._schedule_resume_params = None
        self._schedule_resume_dirty = False
        self._schedule_last_success_ppct = None
        self._schedule_next_ppct = None
        self._schedule_last_summary = None
        self._schedule_summary_window = None
        self._class_stats_thread = None
        self._class_stats_queue = queue.Queue()
        self._class_stats_running = False
        self._auto_login_thread = None
        self._auto_login_running = False
        self._class_stats_dialog = None
        self._class_stats_status_label = None
        self._class_stats_result_text = None
        self._class_stats_buttons_frame = None
        self._class_stats_buttons = {}
        self._class_stats_mon_hoc_options = []
        self._class_stats_lop_records = []
        self.cmb_stats_mon_hoc = None
        self._class_stats_lop_options = []
        self._class_stats_lop_records = []
        self._class_stats_tuan_options = []
        self._class_stats_selected_lop = None
        self._class_stats_fetch_cache = {}
        self._class_stats_fetch_cache_saved_at = {}
        self._class_stats_fetch_cache_lock = threading.Lock()
        self._quick_prepare_running = False
        # ===== Delete (Xóa dữ liệu sổ đầu bài) state =====
        self._delete_thread = None
        self._delete_queue = queue.Queue()
        self._delete_running = False
        self._delete_stop_event = threading.Event()
        self._delete_dialog = None
        self._delete_status_label = None
        self._delete_result_text = None
        self._delete_run_button = None
        self._delete_stop_button = None
        self._delete_scan_button = None
        self._delete_class_combo = None
        self._delete_scanned_entries = []
        self._delete_scanning = False
        # Khóa ngữ cảnh của lần scan gần nhất để chống xóa lệch khi user đổi
        # Lớp / khoảng Tuần / filter sau khi đã quét.
        self._delete_scan_signature = None
        self.var_delete_tuan_from = tk.IntVar(value=1)
        self.var_delete_tuan_to = tk.IntVar(value=1)
        self.var_delete_lop = tk.StringVar(value="")
        self.var_delete_only_mine = tk.BooleanVar(value=True)
        self.var_delete_confirm = tk.StringVar(value="")
        self.var_stats_tuan_from = tk.IntVar(value=1)
        self.var_stats_tuan_to = tk.IntVar(value=1)
        self.var_stats_mon_hoc = tk.StringVar(value="")
        self.var_vnedu_username = tk.StringVar(value="")
        self.var_vnedu_password = tk.StringVar(value="")
        self.var_show_vnedu_password = tk.BooleanVar(value=False)

        # Schedule Tkinter vars — Tuần range + Lớp (single select)
        self.var_sched_tuan_from = tk.IntVar(value=1)
        self.var_sched_tuan_to = tk.IntVar(value=1)
        self.var_sched_lop = tk.StringVar(value="")
        self.var_sched_lop_multi = tk.StringVar(value="")
        # Schedule grid vars — 7 ngày (Thứ 2-7 + CN) × 2 buổi × 5 tiết = 70 checkboxes
        # dict key = (thu, buoi) → list[BooleanVar] cho tiết 1-5
        # thu: 2,3,4,5,6,7,8(CN)  buoi: "S" (Sáng), "C" (Chiều)
        self._sched_grid = {}    # {(thu, buoi): [BooleanVar × 5]}
        self._sched_buoi = {}    # {thu: StringVar} — buổi cho mỗi thứ
        for thu in SCHEDULE_DAYS:
            self._sched_buoi[thu] = tk.StringVar(value="---")
            for buoi_code in ("S", "C"):
                self._sched_grid[(thu, buoi_code)] = [
                    tk.BooleanVar(value=False) for _ in range(5)
                ]
        self._sched_checkbuttons = {}    # {(thu, tiet_idx): Checkbutton widget}

        # Schedule nhập liệu vars — riêng cho CDP fill_form
        # Lưu text hiển thị trên combobox; value thực được resolve từ cache options.
        self.var_sched_mode = tk.StringVar(value=SCHEDULE_MODE_MANUAL)
        self.var_sched_phan_mon = tk.StringVar(value="")
        self.var_sched_mon_hoc = tk.StringVar(value="")
        self.var_sched_ppct_start = tk.IntVar(value=1)    # Tiết PPCT bắt đầu
        self.var_sched_hs_nghi = tk.StringVar(value="0")
        self.var_sched_diem = tk.StringVar(value="10")
        self.var_sched_nhan_xet = tk.StringVar(value="Lớp học chăm ngoan")
        self.var_sched_ppct_runtime = tk.StringVar(value="")
        self.var_sched_teacher_progress_status = tk.StringVar(
            value="Quét dữ liệu lớp/tuần rồi chọn lớp để xem tiến độ PPCT các môn bạn đang dạy."
        )
        self.var_sched_teacher_progress_button = tk.StringVar(
            value="Xem tiến độ PPCT môn đang dạy"
        )
        self.var_sched_teacher_progress_fast_mode = tk.BooleanVar(value=False)
        self.var_sched_live_progress = tk.StringVar(value="● Tiến trình live: chưa có tác vụ nào chạy")
        # Lưu danh sách options đã fetch từ CDP
        self._sched_phan_mon_options = []  # [{value, text}, ...]
        self._sched_phan_mon_by_mon_hoc = {}  # {mon_hoc_value: [{value, text}, ...]}
        self._sched_mon_hoc_options = []   # [{value, text}, ...]
        self._sched_mon_hoc_field = None   # Tên field Môn học trên VnEdu
        self._sched_form_options_cache = {}
        self._sched_form_options_cache_lock = threading.Lock()
        self._sched_form_session_id = 0
        self._sched_form_scan_session_id = None
        self._sched_form_scan_context_key = None
        self._sched_form_rescan_reason = ""
        self._sched_form_last_announced_issue = ""
        self._sched_teacher_progress_after_id = None
        self._sched_teacher_progress_prewarm_after_id = None
        self._sched_teacher_progress_request_id = 0
        self._sched_teacher_progress_payload = None
        self._sched_teacher_progress_window = None
        self._sched_teacher_progress_text = None
        self._sched_teacher_progress_latest_week = None
        self._closing = False
        self._close_started_at = None
        self._ui_task_queue = queue.Queue()
        self._ui_task_pump_after_id = None

        # ===== Build UI =====
        self._setup_ui()
        self._ui_task_pump_after_id = self.root.after(UI_TASK_POLL_MS, self._pump_ui_tasks)

        # ===== Load config =====
        self._load_config()
        self._apply_sched_mode_state()
        self._update_sched_ppct_runtime(
            last_success=(
                self._schedule_resume_state.get("last_success_ppct")
                if self._schedule_resume_state else None
            ),
            next_ppct=(
                self._schedule_resume_state.get("next_ppct", self.var_sched_ppct_start.get())
                if self._schedule_resume_state else self.var_sched_ppct_start.get()
            ),
            status="paused" if self._schedule_resume_state else "idle",
        )
        self._set_schedule_button_states(
            running=False,
            can_resume=bool(self._schedule_resume_state),
        )
        self._set_auto_login_button_state()
        self._set_sched_live_progress(
            current=0,
            total=1,
            phase="Tiến trình live",
            detail="Chưa có tác vụ nào chạy",
            state="idle",
        )

        # ===== Window position =====
        self._position_window()
        self.root.after(250, lambda: self._ensure_window_visible(force=True))
        self.root.after(1200, lambda: self._ensure_window_visible(force=True))

        # ===== Save on close =====
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind_all("<Escape>", self._on_hotkey_escape, add="+")

    # -----------------------------------------------------------------
    # UI SETUP
    # -----------------------------------------------------------------

    def _setup_styles(self):
        """Khai báo các style ttk dùng riêng cho app."""
        try:
            # `clam` cho phép custom màu nền button ổn định hơn trên Windows.
            self.style.theme_use("clam")
        except Exception:
            pass

        base_font = ("Segoe UI", 10)
        button_font = ("Segoe UI", 10, "bold")
        hint_font = ("Segoe UI", 9)

        self.style.configure(".", font=base_font)
        self.style.configure("TFrame", background=UI_BG_APP)
        self.style.configure("TLabel", background=UI_BG_APP, foreground=UI_TEXT, font=base_font)
        self.style.configure(
            "TLabelframe",
            background=UI_BG_APP,
            bordercolor=UI_BORDER,
            relief="solid",
            borderwidth=1,
        )
        self.style.configure(
            "TLabelframe.Label",
            background=UI_BG_APP,
            foreground=UI_TEXT,
            font=("Segoe UI", 10, "bold"),
        )
        self.style.configure(
            "Subtle.TButton",
            background=UI_SURFACE_ALT,
            foreground="#374151",
            padding=(12, 6),
            borderwidth=1,
            font=base_font,
        )
        self.style.map(
            "Subtle.TButton",
            background=[("disabled", UI_DISABLED_BG), ("pressed", "#e5e7eb"), ("active", "#eef2ff")],
            foreground=[("disabled", UI_DISABLED_TEXT)],
        )
        self.style.configure(
            "Primary.TButton",
            background=UI_PRIMARY,
            foreground="#ffffff",
            padding=(14, 7),
            borderwidth=1,
            font=button_font,
        )
        self.style.map(
            "Primary.TButton",
            background=[("disabled", "#bfdbfe"), ("pressed", UI_PRIMARY_ACTIVE), ("active", "#3b82f6")],
            foreground=[("disabled", "#eff6ff")],
        )
        self.style.configure(
            "Danger.TButton",
            background="#fee2e2",
            foreground="#7f1d1d",
            padding=(12, 6),
            borderwidth=1,
            font=button_font,
        )
        self.style.map(
            "Danger.TButton",
            background=[("disabled", "#f5e8e8"), ("pressed", "#fecaca"), ("active", "#fee2e2")],
            foreground=[("disabled", "#a8a29e")],
        )
        self.style.configure(
            "Highlight.TButton",
            background="#fef3c7",
            foreground="#78350f",
            padding=(12, 6),
            borderwidth=1,
            font=base_font,
        )
        self.style.map(
            "Highlight.TButton",
            background=[
                ("disabled", "#f7f0d2"),
                ("pressed", "#fde68a"),
                ("active", "#fef3c7"),
            ],
            foreground=[
                ("disabled", UI_DISABLED_TEXT),
            ],
        )
        self.style.configure(
            "QuickGreen.TButton",
            background=UI_SUCCESS,
            foreground="#ffffff",
            padding=(14, 7),
            borderwidth=1,
            font=button_font,
        )
        self.style.map(
            "QuickGreen.TButton",
            background=[
                ("disabled", "#bbf7d0"),
                ("pressed", UI_SUCCESS_ACTIVE),
                ("active", "#22c55e"),
            ],
            foreground=[
                ("disabled", "#f0fdf4"),
            ],
        )
        self.style.configure(
            "LiveGreen.Horizontal.TProgressbar",
            troughcolor="#e5e7eb",
            background=UI_SUCCESS,
            darkcolor=UI_SUCCESS_ACTIVE,
            lightcolor="#4ade80",
            bordercolor=UI_BORDER,
            thickness=16,
        )
        self.style.configure(
            "LiveGreen.TLabel",
            background=UI_BG_APP,
            foreground="#166534",
            font=("Segoe UI", 9, "bold"),
        )
        self.style.configure(
            "Hint.TLabel",
            background=UI_BG_APP,
            foreground=UI_TEXT_MUTED,
            font=hint_font,
        )

    def _build_schedule_checkbox_image(self, size=18, checked=False, disabled=False):
        """Tạo ảnh checkbox vuông cho grid lịch dạy."""
        img = tk.PhotoImage(width=size, height=size)
        bg = "#ffffff" if not disabled else "#f0f0f0"
        border = "#787878" if not disabled else "#b8b8b8"
        mark = "#111111" if not disabled else "#8a8a8a"

        img.put(bg, to=(0, 0, size, size))
        img.put(border, to=(0, 0, size, 1))
        img.put(border, to=(0, size - 1, size, size))
        img.put(border, to=(0, 0, 1, size))
        img.put(border, to=(size - 1, 0, size, size))

        if checked:
            mark_size = max(8, size - 8)
            start = max(1, (size - mark_size) // 2)
            thickness = 2
            for idx in range(mark_size):
                x = start + idx
                y_main = start + idx
                y_cross = start + mark_size - 1 - idx
                for offset in range(thickness):
                    y1 = y_main + offset
                    y2 = y_cross - offset
                    if 0 <= x < size and 0 <= y1 < size:
                        img.put(mark, to=(x, y1, x + 1, y1 + 1))
                    if 0 <= x < size and 0 <= y2 < size:
                        img.put(mark, to=(x, y2, x + 1, y2 + 1))
        return img

    def _init_schedule_checkbox_images(self):
        """Khởi tạo bộ ảnh checkbox lớn hơn cho grid lịch dạy."""
        self._sched_checkbox_images = {
            "off": self._build_schedule_checkbox_image(checked=False, disabled=False),
            "on": self._build_schedule_checkbox_image(checked=True, disabled=False),
            "off_disabled": self._build_schedule_checkbox_image(checked=False, disabled=True),
            "on_disabled": self._build_schedule_checkbox_image(checked=True, disabled=True),
        }

    def _setup_ui(self):
        """Dựng giao diện 2 panel: trái (CDP + log), phải (schedule)."""

        # === Body frame: chia 2 panel (trái + phải) bằng grid layout ===
        # Grid đảm bảo cả 2 panel luôn có đúng kích thước, không phụ thuộc pack order.
        self._body_frame = ttk.Frame(self.root)
        self._body_frame.pack(fill="both", expand=True)
        self._body_frame.columnconfigure(0, weight=1)   # Panel trái co giãn
        self._body_frame.columnconfigure(1, weight=0, minsize=RIGHT_PANEL_WIDTH)  # Panel phải cố định
        self._body_frame.rowconfigure(0, weight=1)       # Full chiều cao

        # Compact shell nằm ngoài scroll-canvas để không bị mất khỏi viewport.
        self._compact_shell = ttk.Frame(self._body_frame, padding=12)
        self._compact_shell.columnconfigure(0, weight=1)
        self._build_compact_status_panel(self._compact_shell)

        # --- Panel trái: Scrollable container chứa các section chính ---
        self._left_panel = ttk.Frame(self._body_frame)
        self._left_panel.grid(row=0, column=0, sticky="nsew")

        self._canvas = tk.Canvas(self._left_panel, highlightthickness=0)
        self._scrollbar = ttk.Scrollbar(
            self._left_panel, orient="vertical", command=self._canvas.yview
        )
        self._canvas.configure(yscrollcommand=self._scrollbar.set)

        self._scrollbar.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        # Frame bên trong canvas chứa toàn bộ widgets
        self.main_frame = ttk.Frame(self._canvas, padding=6)
        self._canvas_window = self._canvas.create_window(
            (0, 0), window=self.main_frame, anchor="nw"
        )

        # Cập nhật scroll region khi nội dung thay đổi kích thước
        self.main_frame.bind("<Configure>", self._on_frame_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)

        # Hỗ trợ cuộn bằng chuột (mouse wheel)
        # Chỉ bind mousewheel cho canvas (tránh ảnh hưởng Spinbox)
        self._canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.main_frame.bind("<MouseWheel>", self._on_mousewheel)

        # --- 0A. Frame Chrome CDP Connection ---
        self.frame_cdp = ttk.LabelFrame(
            self.main_frame, text="Trình duyệt và đăng nhập", padding=8
        )
        self.frame_cdp.pack(fill="x", pady=(0, 4))
        self._build_cdp_section(self.frame_cdp)

        # --- 1. Buttons ---
        self.frame_buttons = ttk.Frame(self.main_frame)
        self.frame_buttons.pack(anchor="e", pady=(0, 4))
        self._build_buttons(self.frame_buttons)

        self.frame_class_stats = ttk.Frame(self.main_frame)
        self.frame_class_stats.pack(fill="x", pady=(0, 6))
        self._build_class_stats_toolbar(self.frame_class_stats)

        # --- 2. Frame Log ---
        self.frame_log = ttk.LabelFrame(
            self.main_frame, text="Nhật ký", padding=6
        )
        self.frame_log.pack(fill="x", expand=False, pady=(0, 0))
        self._build_log(self.frame_log)

        # === Panel phải: Schedule (Lịch dạy) ===
        # Grid column=1, width cố định = RIGHT_PANEL_WIDTH
        self._right_panel = ttk.Frame(self._body_frame, width=RIGHT_PANEL_WIDTH)
        self._right_panel.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        self._right_panel.grid_propagate(False)  # Giữ width cố định, không co/giãn

        # Scrollable container cho panel phải (tương tự panel trái)
        self._right_canvas = tk.Canvas(self._right_panel, highlightthickness=0)
        self._right_scrollbar = ttk.Scrollbar(
            self._right_panel, orient="vertical", command=self._right_canvas.yview
        )
        self._right_canvas.configure(yscrollcommand=self._right_scrollbar.set)
        self._right_scrollbar.pack(side="right", fill="y")
        self._right_canvas.pack(side="left", fill="both", expand=True)

        # Frame bên trong canvas phải
        self._right_inner = ttk.Frame(self._right_canvas, padding=3)
        self._right_canvas_window = self._right_canvas.create_window(
            (0, 0), window=self._right_inner, anchor="nw"
        )
        # Cập nhật scroll region + width khi nội dung thay đổi
        self._right_inner.bind(
            "<Configure>",
            lambda e: self._right_canvas.configure(
                scrollregion=self._right_canvas.bbox("all")
            )
        )
        self._right_canvas.bind(
            "<Configure>",
            lambda e: self._right_canvas.itemconfig(
                self._right_canvas_window, width=e.width
            )
        )
        # Mouse wheel scroll cho panel phải
        self._right_canvas.bind("<MouseWheel>", self._on_right_mousewheel)
        self._right_inner.bind("<MouseWheel>", self._on_right_mousewheel)

        self.frame_schedule = ttk.LabelFrame(
            self._right_inner, text="Lịch dạy và dữ liệu nhập", padding=8
        )
        self.frame_schedule.pack(fill="both", expand=True, pady=0)
        self._build_schedule_panel(self.frame_schedule)

        # Bind mousewheel cho TẤT CẢ widget con trong panel phải
        # (để scroll hoạt động khi chuột hover lên bất kỳ widget nào)
        self._bind_mousewheel_recursive(self._right_inner, self._on_right_mousewheel)

    def _build_compact_status_panel(self, parent):
        """Mini dashboard dùng riêng cho compact mode.

        Compact mode trước đây ẩn gần hết widget và dễ để lại vùng trống.
        Panel này giữ các thông tin sống còn: trạng thái, progress, dừng an
        toàn và nút mở lại đầy đủ.
        """
        parent.columnconfigure(0, weight=1)

        ttk.Label(
            parent,
            text="Sổ đầu bài tự động",
            font=("Segoe UI", 12, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            parent,
            textvariable=self.var_sched_live_progress,
            style="LiveGreen.TLabel",
            wraplength=360,
            justify="left",
        ).grid(row=1, column=0, sticky="ew", pady=(6, 6))

        self.compact_progressbar = ttk.Progressbar(
            parent,
            mode="determinate",
            length=360,
            style="LiveGreen.Horizontal.TProgressbar",
        )
        self.compact_progressbar.grid(row=2, column=0, sticky="ew")

        action_row = ttk.Frame(parent)
        action_row.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        action_row.columnconfigure(0, weight=1)

        ttk.Button(
            action_row,
            text="Mở đầy đủ",
            style="Primary.TButton",
            command=self._toggle_compact,
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(
            action_row,
            text="Dừng an toàn",
            style="Danger.TButton",
            command=self._on_schedule_stop,
        ).grid(row=0, column=1, sticky="e", padx=(8, 0))

    # ----- Chrome CDP Section -----

    def _build_cdp_section(self, parent):
        """Tạo phần kết nối Chrome CDP.

        Gồm: port input, Connect/Disconnect, status indicator,
        hướng dẫn mở Chrome, nút Inspect Page.
        """
        # Row 1: Port + Connect
        row1 = ttk.Frame(parent)
        row1.pack(fill="x", pady=1)
        row1.columnconfigure(2, weight=1)
        row1.columnconfigure(3, weight=1)
        row1.columnconfigure(4, weight=1)
        ttk.Label(row1, text="Port:").grid(row=0, column=0, sticky="w")
        self.ent_cdp_port = ttk.Entry(
            row1, width=6, textvariable=self.var_cdp_port,
            state="readonly", justify="center"
        )
        self.ent_cdp_port.grid(row=0, column=1, sticky="w", padx=(4, 6))
        ttk.Label(row1, text="(cố định)").grid(row=0, column=2, sticky="w", padx=(0, 6))

        self.btn_cdp_connect = ttk.Button(
            row1, text="Kết nối trình duyệt", command=self._on_cdp_connect,
            style="Subtle.TButton",
        )
        self.btn_cdp_connect.grid(row=0, column=3, sticky="ew", padx=3)

        self.btn_cdp_disconnect = ttk.Button(
            row1, text="Ngắt", command=self._on_cdp_disconnect,
            state="disabled", style="Subtle.TButton",
        )
        self.btn_cdp_disconnect.grid(row=0, column=4, sticky="ew", padx=(3, 0))

        self.btn_quick_prepare = ttk.Button(
            row1,
            text="Quét",
            command=self._on_quick_prepare,
            style="Primary.TButton",
            state="disabled",
        )
        self.btn_quick_prepare.grid(row=0, column=5, sticky="ew", padx=(6, 0))

        # Row 2: Status indicator
        self.lbl_cdp_status = ttk.Label(
            parent, text="○ Chưa kết nối",
            font=("Segoe UI", 10), foreground=UI_TEXT_MUTED
        )
        self.lbl_cdp_status.pack(fill="x", pady=(1, 0))

        row_login = ttk.Frame(parent)
        row_login.pack(fill="x", pady=(5, 0))
        row_login.columnconfigure(1, weight=1)
        row_login.columnconfigure(3, weight=1)
        ttk.Label(row_login, text="TK:").grid(row=0, column=0, sticky="w")
        self.ent_vnedu_username = ttk.Entry(
            row_login, textvariable=self.var_vnedu_username, width=18
        )
        self.ent_vnedu_username.grid(row=0, column=1, sticky="ew", padx=(4, 8))
        ttk.Label(row_login, text="MK:").grid(row=0, column=2, sticky="w")
        self.ent_vnedu_password = ttk.Entry(
            row_login, textvariable=self.var_vnedu_password, width=18, show="*"
        )
        self.ent_vnedu_password.grid(row=0, column=3, sticky="ew", padx=(4, 8))
        self.chk_show_vnedu_password = ttk.Checkbutton(
            row_login,
            text="Hiển thị mật khẩu",
            variable=self.var_show_vnedu_password,
            command=self._on_toggle_vnedu_password_visibility,
        )
        self.chk_show_vnedu_password.grid(row=0, column=4, sticky="w")

        # Row 3: Hướng dẫn + Inspect
        row3 = ttk.Frame(parent)
        row3.pack(fill="x", pady=(5, 0))
        for col_idx in range(5):
            row3.columnconfigure(col_idx, weight=1, uniform="cdp_actions")

        self.btn_launch_chrome = ttk.Button(
            row3, text="Mở Chrome",
            command=self._on_launch_chrome_debug,
            style="Subtle.TButton",
        )
        self.btn_launch_chrome.grid(row=0, column=0, sticky="ew", padx=(0, 3))

        self.btn_copy_chrome_cmd = ttk.Button(
            row3, text="Copy lệnh",
            command=self._copy_chrome_cmd,
            style="Subtle.TButton",
        )
        self.btn_copy_chrome_cmd.grid(row=0, column=1, sticky="ew", padx=3)

        self.btn_inspect = ttk.Button(
            row3, text="Kiểm tra", command=self._on_inspect_page,
            state="disabled", style="Subtle.TButton",
        )
        self.btn_inspect.grid(row=0, column=2, sticky="ew", padx=3)

        self.btn_recover_web = ttk.Button(
            row3, text="Khôi phục", command=self._on_recover_vnedu_ui,
            state="disabled", style="Subtle.TButton",
        )
        self.btn_recover_web.grid(row=0, column=3, sticky="ew", padx=3)

        self.btn_discover_delete = ttk.Button(
            row3, text="Nút xóa", command=self._on_discover_delete_controls,
            state="disabled", style="Subtle.TButton",
        )
        self.btn_discover_delete.grid(row=0, column=4, sticky="ew", padx=(3, 0))

        row_auto = ttk.Frame(parent)
        row_auto.pack(fill="x", pady=(3, 0))
        self.btn_auto_login_run = ttk.Button(
            row_auto,
            text="Đăng nhập và chuẩn bị",
            command=self._on_auto_login_and_run,
            style="QuickGreen.TButton",
        )
        self.btn_auto_login_run.pack(fill="x")
        ttk.Label(
            parent,
            text="Flow: SSO → Quản lý trường học → Sổ đầu bài → Chi tiết sổ đầu bài",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(1, 0))

        self.cdp_live_progressbar = ttk.Progressbar(
            parent,
            mode="determinate",
            length=320,
            style="LiveGreen.Horizontal.TProgressbar",
        )
        self.cdp_live_progressbar.pack(fill="x", pady=(3, 0))
        self.lbl_cdp_live_progress = ttk.Label(
            parent,
            textvariable=self.var_sched_live_progress,
            style="LiveGreen.TLabel",
        )
        self.lbl_cdp_live_progress.pack(fill="x", pady=(1, 0))

        row_delete = ttk.LabelFrame(parent, text="Thao tác rủi ro", padding=6)
        row_delete.pack(fill="x", pady=(8, 0))
        self.btn_open_delete = ttk.Button(
            row_delete,
            text="Xóa dữ liệu sổ đầu bài...",
            command=self._on_open_delete_dialog,
            style="Danger.TButton",
        )
        self.btn_open_delete.pack(fill="x")
        ttk.Label(
            row_delete,
            text="Chỉ dùng sau khi đã quét preview và xác nhận rõ phạm vi xóa.",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(4, 0))

        # Row 4: Thông tin Tuần + Lớp hiện tại trên VnEdu
        self.lbl_cdp_info = ttk.Label(
            parent, text="",
            font=("Segoe UI", 9, "italic"), foreground=UI_TEXT_MUTED
        )
        self.lbl_cdp_info.pack(fill="x", pady=(1, 0))

    def _on_toggle_vnedu_password_visibility(self):
        """Hiện/ẩn mật khẩu VnEdu để hạn chế nhập sai khi thao tác thủ công."""
        if not hasattr(self, "ent_vnedu_password"):
            return
        self.ent_vnedu_password.config(
            show="" if self.var_show_vnedu_password.get() else "*"
        )

    # ----- Schedule Panel (Lịch dạy) -----

    def _build_schedule_panel(self, parent):
        """Tạo panel Lịch dạy — cấu hình Thứ/Buổi/Tiết + Quét & Nhập.

        Layout:
        - Row 1: Tuần range + Lớp single-select combobox
        - Row 2: Nút tải DS Lớp cho schedule
        - Grid: Thứ 2→7, mỗi thứ: Buổi combobox + 5 checkbox Tiết
        - Buttons: Quét & Nhập / Dừng
        - Status label
        """
        if not _HAS_CDP:
            ttk.Label(
                parent,
                text="⚠ Module chrome_bridge.py không tìm thấy.",
                foreground="red", wraplength=300
            ).pack(fill="x")
            return

        # Row 1: Tuần range + Lớp
        row_top = ttk.Frame(parent)
        row_top.pack(fill="x", pady=2)
        row_top.columnconfigure(5, weight=1)
        row_top.columnconfigure(6, weight=0)

        ttk.Label(row_top, text="Tuần:").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(
            row_top, from_=1, to=52, width=4,
            textvariable=self.var_sched_tuan_from
        ).grid(row=0, column=1, sticky="w", padx=(4, 2))
        ttk.Label(row_top, text="→").grid(row=0, column=2, sticky="w", padx=2)
        ttk.Spinbox(
            row_top, from_=1, to=52, width=4,
            textvariable=self.var_sched_tuan_to
        ).grid(row=0, column=3, sticky="w", padx=2)

        ttk.Label(row_top, text="Lớp:").grid(row=0, column=4, sticky="w", padx=(10, 2))
        self.cmb_sched_lop = ttk.Combobox(
            row_top, textvariable=self.var_sched_lop,
            state="readonly", width=10
        )
        self.cmb_sched_lop.grid(row=0, column=5, sticky="ew", padx=(2, 6))
        self.cmb_sched_lop.bind(
            "<<ComboboxSelected>>", self._on_sched_progress_context_changed
        )

        self.btn_sched_load_lop = ttk.Button(
            row_top, text="Tải lớp",
            command=self._on_sched_load_lop, state="disabled"
        )
        self.btn_sched_load_lop.grid(row=0, column=6, sticky="ew")

        row_multi_lop = ttk.Frame(parent)
        row_multi_lop.pack(fill="x", pady=(0, 3))
        ttk.Label(row_multi_lop, text="Lớp KHDH:").pack(side="left")
        self.ent_sched_lop_multi = ttk.Entry(
            row_multi_lop,
            textvariable=self.var_sched_lop_multi,
            width=32,
        )
        self.ent_sched_lop_multi.pack(side="left", padx=4, fill="x", expand=True)
        ttk.Label(
            parent,
            text="💡 Mode KHDH: nhập nhiều lớp cách nhau bằng dấu phẩy. Để trống = dùng lớp đang chọn.",
            style="Hint.TLabel",
            wraplength=360,
            justify="left",
        ).pack(anchor="w", pady=(0, 2))

        # Grid: Thứ × Buổi × Tiết. Ẩn khi chạy theo KHDH vì mode này
        # đọc trực tiếp row đỏ live, không dùng lịch TKB nhập tay.
        self.frame_sched_manual_grid = ttk.Frame(parent)
        self.frame_sched_manual_grid.pack(fill="x", pady=(4, 2))
        grid_frame = ttk.Frame(self.frame_sched_manual_grid)
        grid_frame.pack(fill="x")
        slot_col_pad = 4
        slot_col_minsize = 28
        for col_idx in range(2, 7):
            grid_frame.grid_columnconfigure(col_idx, minsize=slot_col_minsize)

        # Header labels
        ttk.Label(grid_frame, text="Thứ", width=4, anchor="center",
                  font=("Segoe UI", 8, "bold")).grid(row=0, column=0, padx=1)
        ttk.Label(grid_frame, text="Buổi", width=6, anchor="center",
                  font=("Segoe UI", 8, "bold")).grid(row=0, column=1, padx=1)
        for t in range(1, 6):
            ttk.Label(grid_frame, text=f"T{t}", width=3, anchor="center",
                      font=("Segoe UI", 8, "bold")).grid(row=0, column=1+t, padx=slot_col_pad)

        # Data rows — Thứ 2 → CN (7 rows)
        BUOI_OPTIONS = ["---", "Sáng", "Chiều", "Cả hai"]
        for idx, thu in enumerate(SCHEDULE_DAYS):
            row_num = idx + 1

            # Label Thứ (dùng SCHEDULE_DAY_LABELS để hiển thị "CN" cho thu=8)
            day_label = SCHEDULE_DAY_LABELS.get(thu, str(thu))
            ttk.Label(grid_frame, text=day_label, width=4, anchor="center",
                      font=("Segoe UI", 9)).grid(row=row_num, column=0, padx=1, pady=1)

            # Combobox Buổi
            cmb_buoi = ttk.Combobox(
                grid_frame, textvariable=self._sched_buoi[thu],
                values=BUOI_OPTIONS, state="readonly", width=5
            )
            cmb_buoi.grid(row=row_num, column=1, padx=1, pady=1)
            # Khi đổi buổi → enable/disable checkboxes tương ứng
            cmb_buoi.bind("<<ComboboxSelected>>",
                          lambda e, t=thu: self._on_sched_buoi_changed(t))

            # 5 checkboxes Tiết — mặc định dùng buổi Sáng
            for tiet_idx in range(5):
                cb = tk.Checkbutton(
                    grid_frame,
                    variable=self._sched_grid[(thu, "S")][tiet_idx],
                    image=self._sched_checkbox_images["off"],
                    selectimage=self._sched_checkbox_images["on"],
                    indicatoron=False,
                    relief="flat",
                    offrelief="flat",
                    overrelief="flat",
                    borderwidth=0,
                    highlightthickness=0,
                    takefocus=0,
                    bg=self.root.cget("bg"),
                    activebackground=self.root.cget("bg"),
                    disabledforeground="#8a8a8a",
                    padx=0,
                    pady=0,
                )
                cb.grid(row=row_num, column=2+tiet_idx, padx=slot_col_pad, pady=1)
                self._sched_checkbuttons[(thu, tiet_idx)] = cb

        # Chú thích
        hint_row = ttk.Frame(self.frame_sched_manual_grid)
        hint_row.pack(fill="x", pady=(0, 2))
        ttk.Label(
            hint_row, text="💡 Buổi: Sáng/Chiều/Cả hai.",
            style="Hint.TLabel"
        ).pack(side="left")

        row_mode = ttk.Frame(parent)
        self.frame_sched_mode_row = row_mode
        row_mode.pack(fill="x", pady=(0, 4))
        ttk.Label(row_mode, text="Mode chạy:").pack(side="left")
        ttk.Radiobutton(
            row_mode,
            text="Theo TKB cũ",
            value=SCHEDULE_MODE_MANUAL,
            variable=self.var_sched_mode,
            command=self._on_sched_mode_changed,
        ).pack(side="left", padx=(6, 2))
        ttk.Radiobutton(
            row_mode,
            text="Theo KHDH gợi ý",
            value=SCHEDULE_MODE_KHDH,
            variable=self.var_sched_mode,
            command=self._on_sched_mode_changed,
        ).pack(side="left", padx=2)

        # Buttons chính đặt cao trong panel để luôn nhìn thấy ở cả mode KHDH
        # lẫn mode TKB cũ; dữ liệu nhập có thể cuộn bên dưới.
        row_btn = ttk.Frame(parent)
        row_btn.pack(fill="x", pady=(2, 4))
        for col_idx in range(3):
            row_btn.columnconfigure(col_idx, weight=1, uniform="sched_actions")

        self.btn_sched_run = ttk.Button(
            row_btn, text="Quét và nhập",
            style="QuickGreen.TButton",
            command=self._on_schedule_run, state="disabled"
        )
        self.btn_sched_run.grid(row=0, column=0, sticky="ew", padx=(0, 4))

        self.btn_sched_stop = ttk.Button(
            row_btn, text="Dừng an toàn",
            command=self._on_schedule_stop, state="disabled",
            style="Danger.TButton",
        )
        self.btn_sched_stop.grid(row=0, column=1, sticky="ew", padx=4)

        self.btn_sched_resume = ttk.Button(
            row_btn, text="Tiếp tục",
            command=self._on_schedule_resume, state="disabled",
            style="Subtle.TButton",
        )
        self.btn_sched_resume.grid(row=0, column=2, sticky="ew", padx=(4, 0))

        ttk.Label(
            parent,
            text="Esc = dừng an toàn sau slot hiện tại | Tiếp tục = chạy từ checkpoint gần nhất",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(0, 2))

        progress_info_frame = ttk.Frame(parent)
        progress_info_frame.pack(fill="x", pady=(2, 4))

        ttk.Label(
            progress_info_frame,
            text="Tiến độ PPCT môn đang dạy trong lớp đã chọn",
            font=("Segoe UI", 9, "bold"),
            foreground=UI_PRIMARY,
        ).pack(anchor="w")
        ttk.Label(
            progress_info_frame,
            textvariable=self.var_sched_teacher_progress_status,
            style="Hint.TLabel",
            wraplength=355,
            justify="left",
        ).pack(anchor="w", pady=(0, 2))

        self.btn_sched_teacher_progress = ttk.Button(
            progress_info_frame,
            textvariable=self.var_sched_teacher_progress_button,
            command=self._on_sched_teacher_progress_button_click,
            state="disabled",
        )
        self.btn_sched_teacher_progress.pack(fill="x")
        ttk.Checkbutton(
            progress_info_frame,
            text="Fast mode (quét nhanh tiến độ PPCT)",
            variable=self.var_sched_teacher_progress_fast_mode,
            command=self._on_sched_teacher_progress_mode_changed,
        ).pack(anchor="w", pady=(2, 0))

        # ===== CDP Nhập liệu Section =====
        sep_cdp = ttk.Separator(parent, orient="horizontal")
        sep_cdp.pack(fill="x", pady=(4, 4))

        ttk.Label(
            parent, text="Dữ liệu nhập",
            font=("Segoe UI", 9, "bold")
        ).pack(anchor="w")

        self.frame_sched_manual_data = ttk.Frame(parent)
        self.frame_sched_manual_data.pack(fill="x")

        # Row: Môn học combobox
        row_mh = ttk.Frame(self.frame_sched_manual_data)
        row_mh.pack(fill="x", pady=2)
        row_mh.columnconfigure(1, weight=1)
        ttk.Label(row_mh, text="Môn học:").grid(row=0, column=0, sticky="w")
        self.cmb_sched_mon_hoc = ttk.Combobox(
            row_mh, textvariable=self.var_sched_mon_hoc,
            state="readonly", width=20
        )
        self.cmb_sched_mon_hoc.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        self.cmb_sched_mon_hoc.bind(
            "<<ComboboxSelected>>", self._on_sched_mon_hoc_selected
        )

        # Row: Phân môn combobox + nút Quét
        row_pm = ttk.Frame(self.frame_sched_manual_data)
        row_pm.pack(fill="x", pady=2)
        row_pm.columnconfigure(1, weight=1)
        ttk.Label(row_pm, text="Phân môn:").grid(row=0, column=0, sticky="w")
        self.cmb_sched_phan_mon = ttk.Combobox(
            row_pm, textvariable=self.var_sched_phan_mon,
            state="readonly", width=20
        )
        self.cmb_sched_phan_mon.grid(row=0, column=1, sticky="ew", padx=(8, 6))
        self.cmb_sched_phan_mon.bind(
            "<<ComboboxSelected>>", self._on_sched_phan_mon_selected
        )
        self.btn_sched_scan_form = ttk.Button(
            row_pm, text="Quét form",
            command=self._on_sched_scan_form
        )
        self.btn_sched_scan_form.grid(row=0, column=2, sticky="ew")

        # Row: Tiết PPCT nhập tay
        row_ppct = ttk.Frame(self.frame_sched_manual_data)
        row_ppct.pack(fill="x", pady=2)
        ttk.Label(row_ppct, text="PPCT bắt đầu:").pack(side="left")
        self.spn_sched_ppct_start = ttk.Spinbox(
            row_ppct, from_=1, to=200, width=5,
            textvariable=self.var_sched_ppct_start
        )
        self.spn_sched_ppct_start.pack(side="left", padx=4)

        self.lbl_sched_ppct_runtime = ttk.Label(
            self.frame_sched_manual_data,
            textvariable=self.var_sched_ppct_runtime,
            style="Hint.TLabel",
        )
        self.lbl_sched_ppct_runtime.pack(anchor="w", pady=(0, 2))

        self.frame_sched_common_data = ttk.Frame(parent)
        self.frame_sched_common_data.pack(fill="x", pady=(2, 0))
        self.frame_sched_common_data.columnconfigure(1, weight=0)
        self.frame_sched_common_data.columnconfigure(3, weight=0)
        ttk.Label(self.frame_sched_common_data, text="HS nghỉ:").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(
            self.frame_sched_common_data, from_=0, to=50, width=4,
            textvariable=self.var_sched_hs_nghi
        ).grid(row=0, column=1, sticky="w", padx=(4, 14))
        ttk.Label(self.frame_sched_common_data, text="Điểm tiết học:").grid(row=0, column=2, sticky="w")
        ttk.Spinbox(
            self.frame_sched_common_data, from_=0, to=10, width=4,
            textvariable=self.var_sched_diem
        ).grid(row=0, column=3, sticky="w", padx=(4, 0))

        # Row: Nhận xét GV (pipe separated for random)
        row_nx = ttk.Frame(parent)
        row_nx.pack(fill="x", pady=2)
        row_nx.columnconfigure(1, weight=1)
        ttk.Label(row_nx, text="Nhận xét:").grid(row=0, column=0, sticky="w")
        self.ent_sched_nhan_xet = ttk.Entry(
            row_nx, textvariable=self.var_sched_nhan_xet, width=28
        )
        self.ent_sched_nhan_xet.grid(row=0, column=1, sticky="ew", padx=(8, 0))

        ttk.Label(
            parent, text="💡 Nhiều nhận xét ngẫu nhiên: cách bởi dấu |",
            style="Hint.TLabel"
        ).pack(anchor="w")

        # Runtime status của Schedule vẫn giữ nội bộ để worker/update logic không gãy,
        # nhưng không render trên GUI vì panel CDP + Log đã đủ thông tin.
        self._sched_runtime_status_frame = ttk.Frame(parent)

        self.lbl_sched_progress = ttk.Label(
            self._sched_runtime_status_frame, text="", font=("Segoe UI", 9)
        )
        self.lbl_sched_progress.pack(fill="x", pady=(2, 0))

        self.sched_progressbar = ttk.Progressbar(
            self._sched_runtime_status_frame,
            mode="determinate",
            length=300,
            style="LiveGreen.Horizontal.TProgressbar",
        )
        self.sched_progressbar.pack(fill="x", pady=(2, 0))
        self.lbl_sched_live_progress = ttk.Label(
            self._sched_runtime_status_frame,
            textvariable=self.var_sched_live_progress,
            style="LiveGreen.TLabel",
        )
        self.lbl_sched_live_progress.pack(fill="x", pady=(2, 0))

    def _on_sched_buoi_changed(self, thu):
        """Callback khi người dùng đổi Buổi cho 1 Thứ.

        Cập nhật checkboxes hiển thị theo buổi đã chọn.
        Nếu '---' → disable tất cả checkboxes của thứ đó.
        Nếu 'Sáng'/'Chiều' → hiển thị checkboxes cho buổi tương ứng.
        Nếu 'Cả hai' → hiển thị checkboxes cho buổi Sáng (Chiều dùng cùng config).

        Args:
            thu: int — thứ (2-7)
        """
        buoi_text = self._sched_buoi[thu].get()

        for tiet_idx in range(5):
            cb = self._sched_checkbuttons.get((thu, tiet_idx))
            if cb is None:
                continue

            if buoi_text == "---":
                # Disable — không dạy thứ này
                cb.config(
                    state="disabled",
                    image=self._sched_checkbox_images["off_disabled"],
                    selectimage=self._sched_checkbox_images["on_disabled"],
                )
            else:
                cb.config(
                    state="normal",
                    image=self._sched_checkbox_images["off"],
                    selectimage=self._sched_checkbox_images["on"],
                )

                # Rebind checkbutton variable theo buổi
                if buoi_text == "Sáng":
                    cb.config(variable=self._sched_grid[(thu, "S")][tiet_idx])
                elif buoi_text == "Chiều":
                    cb.config(variable=self._sched_grid[(thu, "C")][tiet_idx])
                else:
                    # "Cả hai" — dùng biến Sáng (Chiều sẽ copy khi chạy)
                    cb.config(variable=self._sched_grid[(thu, "S")][tiet_idx])

    def _get_sched_mode(self):
        """Đọc mode schedule hiện tại trên UI."""
        mode = str(self.var_sched_mode.get() or "").strip().lower()
        return mode if mode in {SCHEDULE_MODE_MANUAL, SCHEDULE_MODE_KHDH} else SCHEDULE_MODE_MANUAL

    def _is_sched_khdh_mode(self):
        """True khi schedule đang chạy theo các gợi ý KHDH live."""
        return self._get_sched_mode() == SCHEDULE_MODE_KHDH

    def _get_sched_mode_invalid_reason(self, mode=None):
        """Trả về lý do block chạy/resume cho mode schedule tương ứng."""
        resolved_mode = str(mode or self._get_sched_mode() or "").strip().lower()
        if resolved_mode == SCHEDULE_MODE_KHDH:
            lop_list = self._get_sched_lop_list(SCHEDULE_MODE_KHDH)
            if not lop_list:
                return "Mode KHDH yêu cầu chọn Lớp trước khi chạy."
            return ""
        return self._get_sched_form_invalid_reason()

    def _apply_sched_mode_state(self):
        """Đổi trạng thái widget theo mode schedule hiện tại."""
        khdh_mode = self._is_sched_khdh_mode()
        manual_state = "disabled" if khdh_mode else "readonly"
        button_state = "disabled" if khdh_mode else "normal"
        spin_state = "disabled" if khdh_mode else "normal"

        for widget in (
            getattr(self, "cmb_sched_mon_hoc", None),
            getattr(self, "cmb_sched_phan_mon", None),
        ):
            if widget is not None:
                try:
                    widget.config(state=manual_state)
                except Exception:
                    pass

        for widget in (
            getattr(self, "btn_sched_scan_form", None),
            getattr(self, "spn_sched_ppct_start", None),
        ):
            if widget is not None:
                try:
                    widget.config(state=button_state if widget == getattr(self, "btn_sched_scan_form", None) else spin_state)
                except Exception:
                    pass

        multi_lop_entry = getattr(self, "ent_sched_lop_multi", None)
        if multi_lop_entry is not None:
            try:
                multi_lop_entry.config(state="normal" if khdh_mode else "disabled")
            except Exception:
                pass

        manual_grid = getattr(self, "frame_sched_manual_grid", None)
        mode_row = getattr(self, "frame_sched_mode_row", None)
        if manual_grid is not None:
            try:
                if khdh_mode:
                    manual_grid.pack_forget()
                elif not manual_grid.winfo_manager():
                    pack_kwargs = {"fill": "x", "pady": (4, 2)}
                    if mode_row is not None and mode_row.winfo_manager():
                        pack_kwargs["before"] = mode_row
                    manual_grid.pack(**pack_kwargs)
            except Exception:
                pass

        manual_data = getattr(self, "frame_sched_manual_data", None)
        common_data = getattr(self, "frame_sched_common_data", None)
        if manual_data is not None:
            try:
                if khdh_mode:
                    manual_data.pack_forget()
                elif not manual_data.winfo_manager():
                    pack_kwargs = {"fill": "x"}
                    if common_data is not None and common_data.winfo_manager():
                        pack_kwargs["before"] = common_data
                    manual_data.pack(**pack_kwargs)
            except Exception:
                pass

    def _on_sched_mode_changed(self):
        """Khi đổi mode schedule thì cập nhật guidance và validation tương ứng."""
        self._apply_sched_mode_state()
        if self._is_sched_khdh_mode():
            self._log(
                "Mode schedule: Theo KHDH gợi ý. Grid Thứ/Buổi/Tiết và Môn/Phân môn/PPCT nhập tay sẽ bị bỏ qua.",
                "info",
            )
        else:
            self._log(
                "Mode schedule: Theo TKB cũ. App sẽ dùng grid slot + Môn/Phân môn/PPCT nhập tay.",
                "info",
            )
        if (
            self._schedule_resume_params
            and str(
                self._schedule_resume_params.get("schedule_mode", SCHEDULE_MODE_MANUAL)
                or SCHEDULE_MODE_MANUAL
            ).strip().lower() != self._get_sched_mode()
        ):
            self._log(
                "Checkpoint schedule hiện có thuộc mode khác với mode đang chọn. "
                "Nếu bấm Tiếp tục, app sẽ tự quay về mode của checkpoint để tránh chạy lệch.",
                "warning",
            )
        self._set_schedule_button_states(
            running=self._schedule_running,
            can_resume=bool(self._schedule_resume_state),
        )

    @staticmethod
    def _sort_lop_options(options):
        """Sort tên lớp tự nhiên: 6A4 trước 6A10, giữ text gốc."""
        def _key(value):
            text = str(value or "").strip()
            parts = re.split(r"(\d+)", text.casefold())
            key = []
            for part in parts:
                if part.isdigit():
                    key.append((0, int(part)))
                else:
                    key.append((1, part))
            return key

        cleaned = []
        seen = set()
        for item in list(options or []):
            text = str(item or "").strip()
            if not text or text.startswith("--"):
                continue
            norm = text.casefold()
            if norm in seen:
                continue
            seen.add(norm)
            cleaned.append(text)
        return sorted(cleaned, key=_key)

    def _get_sched_lop_list(self, mode=None):
        """Đọc danh sách lớp schedule; KHDH cho phép nhiều lớp trong ô riêng."""
        resolved_mode = str(mode or self._get_sched_mode() or "").strip().lower()
        raw_items = []
        if resolved_mode == SCHEDULE_MODE_KHDH:
            multi_text = str(self.var_sched_lop_multi.get() or "").strip()
            if multi_text:
                raw_items.extend(re.split(r"[,;\\n]+", multi_text))
        if not raw_items:
            raw_items.append(self.var_sched_lop.get())

        result = []
        seen = set()
        for item in raw_items:
            text = str(item or "").strip()
            if not text:
                continue
            norm = text.casefold()
            if norm in seen:
                continue
            seen.add(norm)
            result.append(text)
        return result

    @staticmethod
    def _normalize_person_name(text):
        """Chuẩn hóa tên người để so khớp ổn định không phụ thuộc dấu/case."""
        value = str(text or "").replace("\n", " ")
        value = unicodedata.normalize("NFD", value)
        value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
        value = re.sub(r"\s+", " ", value).strip().casefold()
        return value

    @staticmethod
    def _schedule_buoi_sort_key(buoi_text):
        """Cho khóa sắp xếp buổi học để so row trong cùng tuần."""
        normalized = AutoDaNangApp._normalize_person_name(buoi_text)
        if "sang" in normalized:
            return 0
        if "chieu" in normalized:
            return 1
        return 2

    def _schedule_occurrence_sort_key(self, occurrence):
        """Khóa so sánh ổn định cho một occurrence trong lịch."""
        week = int(occurrence.get("week", 0) or 0)
        raw_thu = str(occurrence.get("thu", "")).strip().upper()
        if raw_thu == "CN":
            thu_num = 8
        else:
            match = re.search(r"\d+", raw_thu)
            thu_num = int(match.group()) if match else 0
        buoi_num = self._schedule_buoi_sort_key(occurrence.get("buoi", ""))
        tiet_match = re.search(r"\d+", str(occurrence.get("tiet", "")).strip())
        tiet_num = int(tiet_match.group()) if tiet_match else 0
        return (week, thu_num, buoi_num, tiet_num)

    def _set_sched_teacher_progress_button(self, text, state="normal"):
        """Đồng bộ text/state của nút mở chi tiết tiến độ PPCT."""
        self.var_sched_teacher_progress_button.set(str(text or "").strip())
        if getattr(self, "btn_sched_teacher_progress", None) is not None:
            try:
                self.btn_sched_teacher_progress.config(state=state)
            except Exception:
                pass

    def _get_sched_teacher_progress_mode_label(self):
        """Trả về nhãn mode quét tiến độ PPCT hiện tại."""
        return "FAST" if bool(self.var_sched_teacher_progress_fast_mode.get()) else "CHUẨN"

    def _on_sched_teacher_progress_mode_changed(self):
        """Khi đổi mode quét tiến độ PPCT thì trigger prewarm lại cho lớp hiện tại."""
        mode_label = self._get_sched_teacher_progress_mode_label()
        self._log(f"Tiến độ PPCT: chuyển mode {mode_label}.", "info")
        self._on_sched_progress_context_changed()

    def _render_sched_teacher_progress_detail(self):
        """Render chi tiết tiến độ PPCT vào cửa sổ popup nếu đang mở."""
        text_widget = getattr(self, "_sched_teacher_progress_text", None)
        if text_widget is None or not text_widget.winfo_exists():
            return

        payload = self._sched_teacher_progress_payload or {}
        text_widget.config(state="normal")
        text_widget.delete("1.0", "end")
        text_widget.tag_configure("title", foreground="#375a7f", font=("Segoe UI", 9, "bold"))
        text_widget.tag_configure("ok", foreground="#1f6d2d")
        text_widget.tag_configure("warn", foreground="#a86400")
        text_widget.tag_configure("error", foreground="#b00020")
        text_widget.tag_configure("muted", foreground="#666666")

        status = str(payload.get("status", "") or "").strip().lower()
        lop_text = str(payload.get("lop", "")).strip()
        upper_week = int(payload.get("upper_week", 0) or 0)
        teacher_name = str(payload.get("teacher_name", "")).strip() or "không rõ giáo viên"

        if not payload:
            text_widget.insert("end", "Chưa có dữ liệu tiến độ PPCT để hiển thị.\n", "muted")
        elif status == "loading":
            text_widget.insert("end", "Đang quét tiến độ PPCT...\n", "warn")
            text_widget.insert(
                "end",
                f"Lớp: {lop_text} | Đến Tuần {upper_week}\n",
                "muted",
            )
        elif status == "error":
            text_widget.insert("end", "Không đọc được tiến độ PPCT.\n", "error")
            text_widget.insert(
                "end",
                str(payload.get("message", "Lỗi không xác định")) + "\n",
                "error",
            )
        else:
            subjects = list(payload.get("subjects") or [])
            weeks_requested = int(payload.get("weeks_requested", 0) or 0)
            weeks_scanned = int(payload.get("weeks_scanned", 0) or 0)
            scan_stopped_early = bool(payload.get("scan_stopped_early", False))
            cache_hits = int(payload.get("cache_hits", 0) or 0)
            bulk_hits = int(payload.get("bulk_hits", 0) or 0)
            fallback_hits = int(payload.get("fallback_hits", 0) or 0)
            requested_concurrency = int(payload.get("requested_concurrency", 0) or 0)
            effective_concurrency = int(payload.get("effective_concurrency", 0) or 0)
            fast_mode = bool(payload.get("fast_mode", False))
            week_errors = list(payload.get("week_errors") or [])

            text_widget.insert("end", "Tiến độ PPCT các môn bạn đang dạy\n", "title")
            text_widget.insert(
                "end",
                (
                    f"Giáo viên: {teacher_name}\n"
                    f"Lớp: {lop_text}\n"
                    f"Tổng hợp đến: Tuần {upper_week}\n"
                    f"Mode quét: {'FAST' if fast_mode else 'CHUẨN'}\n\n"
                ),
                "muted",
            )

            if not subjects:
                text_widget.insert(
                    "end",
                    "Không phát hiện môn nào có Ký tên khớp với giáo viên hiện tại trong phạm vi đã quét.\n",
                    "warn",
                )
            else:
                for item in subjects:
                    ppct_text = str(item.get("ppct", "--"))
                    mon_hoc = str(item.get("mon_hoc", "(Không rõ môn)"))
                    week_text = str(item.get("week_text", ""))
                    slot_label = str(item.get("slot_label", ""))
                    text_widget.insert("end", f"• {mon_hoc}\n", "title")
                    text_widget.insert(
                        "end",
                        f"  PPCT cuối: {ppct_text}\n  Vị trí: {week_text} | {slot_label}\n\n",
                        "ok",
                    )

            if week_errors:
                text_widget.insert("end", "Tuần đọc lỗi\n", "title")
                for item in week_errors[:10]:
                    text_widget.insert(
                        "end",
                        f"• Tuần {item.get('week', '?')}: {item.get('message', '')}\n",
                        "warn",
                    )
                if len(week_errors) > 10:
                    text_widget.insert(
                        "end",
                        f"... còn {len(week_errors) - 10} tuần lỗi chưa liệt kê\n",
                        "warn",
                    )
                text_widget.insert("end", "\n")

            text_widget.insert(
                "end",
                (
                    f"Đã quét {weeks_scanned}/{weeks_requested or weeks_scanned} tuần"
                    + (" | dừng sớm" if scan_stopped_early else "")
                    + f" | Cache hit {cache_hits} | Bulk {bulk_hits} | Fallback {fallback_hits}"
                    + (
                        f" | Concurrency {effective_concurrency}/{requested_concurrency}"
                        if (requested_concurrency > 0 and effective_concurrency > 0) else ""
                    )
                    + "\n"
                ),
                "muted",
            )

        text_widget.config(state="disabled")

    def _open_sched_teacher_progress_dialog(self):
        """Mở popup chi tiết tiến độ PPCT theo lớp đang chọn."""
        payload = self._sched_teacher_progress_payload or {}

        if self._sched_teacher_progress_window and self._sched_teacher_progress_window.winfo_exists():
            self._sched_teacher_progress_window.deiconify()
            self._sched_teacher_progress_window.lift()
            self._sched_teacher_progress_window.focus_force()
            self._render_sched_teacher_progress_detail()
            return

        window = tk.Toplevel(self.root)
        window.title("Tiến độ PPCT môn đang dạy")
        window.transient(self.root)
        window.geometry("700x420")
        window.minsize(560, 320)
        self._sched_teacher_progress_window = window

        header = ttk.Frame(window, padding=10)
        header.pack(fill="x")
        ttk.Label(
            header,
            text="Tiến độ PPCT môn bạn đang dạy trong lớp đã chọn",
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            header,
            textvariable=self.var_sched_teacher_progress_status,
            foreground="#666",
            wraplength=650,
            justify="left",
        ).pack(anchor="w", pady=(2, 0))

        text_widget = tk.Text(window, wrap="word", font=("Segoe UI", 9))
        text_widget.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self._sched_teacher_progress_text = text_widget

        footer = ttk.Frame(window, padding=(10, 0, 10, 10))
        footer.pack(fill="x")
        ttk.Button(footer, text="Đóng", command=window.destroy).pack(side="right")

        def _on_close():
            try:
                window.destroy()
            finally:
                self._sched_teacher_progress_window = None
                self._sched_teacher_progress_text = None

        window.protocol("WM_DELETE_WINDOW", _on_close)
        self._render_sched_teacher_progress_detail()

    def _on_sched_teacher_progress_button_click(self):
        """Mở chi tiết tiến độ hoặc chủ động refresh khi chưa có payload mới nhất."""
        current_lop = self.var_sched_lop.get().strip()
        if not current_lop:
            self._log("Chưa chọn lớp để xem tiến độ PPCT.", "warning")
            return
        if not self._cdp_connected:
            self._log("Chưa kết nối CDP!", "warning")
            return

        payload = self._sched_teacher_progress_payload or {}
        payload_lop = str(payload.get("lop", "")).strip()
        payload_status = str(payload.get("status", "") or "").strip().lower()

        if self._quick_prepare_running or self._schedule_running or self._auto_login_running:
            if payload:
                self._open_sched_teacher_progress_dialog()
            return

        if (not payload) or payload_lop != current_lop or payload_status in {"", "error"}:
            self._request_sched_teacher_progress_refresh()
        self._open_sched_teacher_progress_dialog()

    def _cancel_sched_teacher_progress_jobs(self):
        """Hủy các lịch refresh/prewarm đang chờ để tránh worker cũ ghi đè trạng thái mới."""
        if self._sched_teacher_progress_after_id:
            try:
                self.root.after_cancel(self._sched_teacher_progress_after_id)
            except Exception:
                pass
            self._sched_teacher_progress_after_id = None
        if self._sched_teacher_progress_prewarm_after_id:
            try:
                self.root.after_cancel(self._sched_teacher_progress_prewarm_after_id)
            except Exception:
                pass
            self._sched_teacher_progress_prewarm_after_id = None

    def _schedule_sched_teacher_progress_prewarm(self, delay_ms=350):
        """Lên lịch prewarm nền cho tiến độ PPCT và cache form của lớp đang chọn."""
        if self._closing or not self._root_exists():
            return
        self._cancel_sched_teacher_progress_jobs()

        try:
            delay_ms = max(0, int(delay_ms))
        except Exception:
            delay_ms = 0

        self._sched_teacher_progress_prewarm_after_id = self.root.after(
            delay_ms,
            self._request_sched_teacher_progress_prewarm,
        )

    def _render_sched_teacher_progress(self, payload):
        """Hiển thị kết quả tóm tắt tiến độ PPCT ngay trong panel schedule."""
        self._sched_teacher_progress_payload = copy.deepcopy(payload) if payload else None
        if not payload:
            default_state = "normal" if (self._cdp_connected and self.var_sched_lop.get().strip()) else "disabled"
            if default_state == "normal":
                status_text = "Đã sẵn sàng. Bấm nút bên dưới hoặc chọn lại lớp để quét tiến độ PPCT."
            else:
                status_text = "Quét FULL DỮ LIỆU rồi chọn lớp để xem tiến độ PPCT các môn bạn đang dạy."
            self.var_sched_teacher_progress_status.set(status_text)
            self._set_sched_teacher_progress_button(
                "📌 XEM / CẬP NHẬT TIẾN ĐỘ PPCT MÔN ĐANG DẠY",
                state=default_state,
            )
            self._render_sched_teacher_progress_detail()
            return

        status = str(payload.get("status", "") or "").strip().lower()
        lop_text = str(payload.get("lop", "")).strip()
        upper_week = int(payload.get("upper_week", 0) or 0)
        teacher_name = str(payload.get("teacher_name", "")).strip()

        if status == "loading":
            mode_label = "FAST" if bool(payload.get("fast_mode", False)) else "CHUẨN"
            if upper_week > 0:
                loading_text = (
                    f"Đang quét lớp {lop_text} đến Tuần {upper_week} "
                    f"(mode {mode_label}) cho giáo viên {teacher_name or 'hiện tại'}..."
                )
            else:
                loading_text = (
                    f"Đang quét lớp {lop_text} theo tuần mới nhất đang có trên VnEdu "
                    f"(mode {mode_label})..."
                )
            self.var_sched_teacher_progress_status.set(loading_text)
            self._set_sched_teacher_progress_button(
                "⏳ ĐANG QUÉT TIẾN ĐỘ PPCT...",
                state="disabled",
            )
            self._render_sched_teacher_progress_detail()
            return

        if status == "error":
            self.var_sched_teacher_progress_status.set(
                f"Không đọc được tiến độ PPCT của lớp {lop_text}."
            )
            self._set_sched_teacher_progress_button(
                "⚠ XEM LỖI QUÉT TIẾN ĐỘ PPCT",
                state="normal",
            )
            self._render_sched_teacher_progress_detail()
            return

        subjects = list(payload.get("subjects") or [])
        weeks_requested = int(payload.get("weeks_requested", 0) or 0)
        weeks_scanned = int(payload.get("weeks_scanned", 0) or 0)
        scan_stopped_early = bool(payload.get("scan_stopped_early", False))
        cache_hits = int(payload.get("cache_hits", 0) or 0)
        bulk_hits = int(payload.get("bulk_hits", 0) or 0)
        requested_concurrency = int(payload.get("requested_concurrency", 0) or 0)
        effective_concurrency = int(payload.get("effective_concurrency", 0) or 0)
        fast_mode = bool(payload.get("fast_mode", False))
        week_errors = list(payload.get("week_errors") or [])
        teacher_display = teacher_name or "không rõ giáo viên"
        mode_label = "FAST" if fast_mode else "CHUẨN"

        if not subjects:
            self.var_sched_teacher_progress_status.set(
                f"{teacher_display} | Lớp {lop_text} | Mode {mode_label} | Tổng hợp đến Tuần {upper_week} | 0 môn"
            )
            self._set_sched_teacher_progress_button(
                "📌 KHÔNG PHÁT HIỆN MÔN NÀO - BẤM ĐỂ XEM CHI TIẾT",
                state="normal",
            )
            self._render_sched_teacher_progress_detail()
            return

        self.var_sched_teacher_progress_status.set(
            f"{teacher_display} | Lớp {lop_text} | Mode {mode_label} | Tổng hợp đến Tuần {upper_week} | {len(subjects)} môn"
        )
        top_item = subjects[0]
        top_mon_hoc = str(top_item.get("mon_hoc", "(Không rõ môn)"))
        top_ppct = str(top_item.get("ppct", "--"))
        button_text = (
            f"📌 {top_mon_hoc} | PPCT {top_ppct}"
            f" | {top_item.get('week_text', '')}"
            f" | {top_item.get('slot_label', '')}"
        )
        if len(subjects) > 1:
            button_text += f" | +{len(subjects) - 1} môn"
        if week_errors:
            button_text += f" | lỗi tuần: {len(week_errors)}"
        button_text += f" | quét {weeks_scanned}/{weeks_requested or weeks_scanned} tuần"
        if scan_stopped_early:
            button_text += " | dừng sớm"
        if cache_hits:
            button_text += f" | cache {cache_hits}"
        if bulk_hits:
            button_text += f" | bulk {bulk_hits}"
        if requested_concurrency > 0 and effective_concurrency > 0:
            button_text += f" | c{effective_concurrency}/{requested_concurrency}"
        self._set_sched_teacher_progress_button(button_text, state="normal")
        self._render_sched_teacher_progress_detail()

    @staticmethod
    def _parse_week_number(text):
        """Tách số tuần từ text như 'Tuần 24'."""
        match = re.search(r"\d+", str(text or ""))
        if not match:
            return None
        try:
            return int(match.group())
        except Exception:
            return None

    def _resolve_sched_progress_latest_week(self, bridge):
        """Lấy tuần mới nhất đang có trên VnEdu, không phụ thuộc tuần nhập trên GUI."""
        ok_tuan, tuan_options_or_error = bridge.get_tuan_options()
        if ok_tuan:
            numbers = [
                num for num in
                (self._parse_week_number(item) for item in list(tuan_options_or_error or []))
                if num is not None
            ]
            if numbers:
                latest_week = max(numbers)
                self._sched_teacher_progress_latest_week = latest_week
                return latest_week

        ok_current, current_info_or_error = bridge.get_current_selection()
        if ok_current:
            latest_week = self._parse_week_number((current_info_or_error or {}).get("tuan", ""))
            if latest_week is not None:
                self._sched_teacher_progress_latest_week = latest_week
                return latest_week

        if self._sched_teacher_progress_latest_week is not None:
            return int(self._sched_teacher_progress_latest_week)
        return None

    def _on_sched_progress_context_changed(self, *_args):
        """Debounce prewarm nền vùng tiến độ PPCT khi đổi lớp hoặc sau các bước prepare."""
        lop_text = self.var_sched_lop.get().strip()
        self._set_schedule_button_states(
            running=self._schedule_running,
            can_resume=bool(self._schedule_resume_state),
        )
        current_issue = self._get_sched_form_invalid_reason()
        if (
            current_issue.startswith("Bạn đã đổi Lớp")
            and current_issue != self._sched_form_last_announced_issue
        ):
            self._sched_form_last_announced_issue = current_issue
            self._log(f"⚠ {current_issue}", "warning")
        if not lop_text:
            self._cancel_sched_teacher_progress_jobs()
            self._render_sched_teacher_progress(None)
            return

        if not self._cdp_connected:
            self._cancel_sched_teacher_progress_jobs()
            self._render_sched_teacher_progress(None)
            return

        mode_label = self._get_sched_teacher_progress_mode_label()
        payload = self._sched_teacher_progress_payload or {}
        payload_lop = str(payload.get("lop", "")).strip()
        payload_status = str(payload.get("status", "") or "").strip().lower()
        if payload_lop != lop_text or payload_status in {"", "error"}:
            self.var_sched_teacher_progress_status.set(
                f"Đang chuẩn bị dữ liệu tiến độ PPCT nền ({mode_label}) cho lớp {lop_text}..."
            )
            self._set_sched_teacher_progress_button(
                "⏳ ĐANG CHUẨN BỊ TIẾN ĐỘ PPCT...",
                state="normal",
            )
        self._schedule_sched_teacher_progress_prewarm(delay_ms=350)

    def _request_sched_teacher_progress_prewarm(self):
        """Prewarm cache form và tiến độ PPCT trong nền, không khóa UI."""
        self._sched_teacher_progress_prewarm_after_id = None
        lop_text = self.var_sched_lop.get().strip()

        if not self._cdp_connected:
            return
        if self._schedule_running or self._quick_prepare_running or self._auto_login_running:
            return
        if not lop_text:
            return

        existing_payload = copy.deepcopy(self._sched_teacher_progress_payload or {})
        existing_lop = str(existing_payload.get("lop", "")).strip()
        existing_status = str(existing_payload.get("status", "") or "").strip().lower()
        fast_mode = bool(self.var_sched_teacher_progress_fast_mode.get())

        self._sched_teacher_progress_request_id += 1
        request_id = self._sched_teacher_progress_request_id

        def _work():
            bridge = None
            payload = None
            try:
                bridge = ChromeBridge(port=self._cdp_port)
                ok, msg = bridge.connect()
                if not ok:
                    return

                ok_current, current_info = bridge.get_current_selection()
                if ok_current and isinstance(current_info, dict):
                    current_lop = str(current_info.get("lop", "")).strip()
                    cached_form = self._get_sched_form_options_cache(current_info)
                    if current_lop and current_lop == lop_text and cached_form is None:
                        ok_form, form_payload = bridge.read_form_options(row_index=0)
                        if ok_form:
                            self._set_sched_form_options_cache(current_info, form_payload)

                resolved_upper_week = self._resolve_sched_progress_latest_week(bridge)
                if resolved_upper_week is None:
                    return

                resolved_upper_week_num = int(resolved_upper_week)
                self._sched_teacher_progress_latest_week = resolved_upper_week_num

                payload_upper_week = int(existing_payload.get("upper_week", 0) or 0)
                payload_fast_mode = bool(existing_payload.get("fast_mode", False))
                if (
                    existing_payload
                    and existing_lop == lop_text
                    and existing_status == "ok"
                    and payload_upper_week == resolved_upper_week_num
                    and payload_fast_mode == fast_mode
                ):
                    payload = copy.deepcopy(existing_payload)
                else:
                    ok_user, teacher_name_or_error = bridge.get_current_user_full_name()
                    if not ok_user:
                        return
                    payload = self._collect_sched_teacher_progress_report(
                        bridge=bridge,
                        lop_text=lop_text,
                        upper_week=resolved_upper_week_num,
                        teacher_name=str(teacher_name_or_error),
                        fast_mode=fast_mode,
                    )
                    payload["status"] = "ok"
            except Exception as e:
                self._post_ui(
                    lambda e=e: self._log(
                        f"Prewarm tiến độ PPCT lỗi: {type(e).__name__}: {str(e)[:160]}",
                        "warning",
                    )
                )
                return
            finally:
                if bridge:
                    try:
                        bridge.disconnect()
                    except Exception:
                        pass

            def _apply():
                if request_id != self._sched_teacher_progress_request_id:
                    return
                if self.var_sched_lop.get().strip() != lop_text:
                    return
                if payload:
                    self._render_sched_teacher_progress(payload)

            self._post_ui(_apply)

        threading.Thread(target=_work, daemon=True).start()

    def _request_sched_teacher_progress_refresh(self):
        """Khởi chạy worker quét nhanh các môn của giáo viên trong lớp đang chọn."""
        self._cancel_sched_teacher_progress_jobs()
        lop_text = self.var_sched_lop.get().strip()
        upper_week = int(self._sched_teacher_progress_latest_week or 0)
        fast_mode = bool(self.var_sched_teacher_progress_fast_mode.get())

        if not self._cdp_connected:
            self._render_sched_teacher_progress(None)
            return
        if self._schedule_running or self._quick_prepare_running or self._auto_login_running:
            return
        if not lop_text:
            self._render_sched_teacher_progress(None)
            return

        self._sched_teacher_progress_request_id += 1
        request_id = self._sched_teacher_progress_request_id
        self._render_sched_teacher_progress({
            "status": "loading",
            "lop": lop_text,
            "upper_week": upper_week,
            "fast_mode": fast_mode,
        })

        def _work():
            bridge = None
            upper_week_hint = upper_week
            try:
                bridge = ChromeBridge(port=self._cdp_port)
                ok, msg = bridge.connect()
                if not ok:
                    payload = {
                        "status": "error",
                        "lop": lop_text,
                        "upper_week": upper_week_hint,
                        "message": f"Kết nối CDP thất bại: {msg}",
                    }
                else:
                    ok_user, teacher_name_or_error = bridge.get_current_user_full_name()
                    if not ok_user:
                        payload = {
                            "status": "error",
                            "lop": lop_text,
                            "upper_week": upper_week_hint,
                            "message": str(teacher_name_or_error),
                        }
                    else:
                        resolved_upper_week = self._resolve_sched_progress_latest_week(bridge)
                        if resolved_upper_week is None:
                            payload = {
                                "status": "error",
                                "lop": lop_text,
                                "upper_week": upper_week_hint,
                                "message": "Không đọc được tuần mới nhất từ VnEdu",
                            }
                        else:
                            resolved_upper_week_num = int(resolved_upper_week)
                            self._sched_teacher_progress_latest_week = resolved_upper_week_num
                            payload = self._collect_sched_teacher_progress_report(
                                bridge=bridge,
                                lop_text=lop_text,
                                upper_week=resolved_upper_week_num,
                                teacher_name=str(teacher_name_or_error),
                                fast_mode=fast_mode,
                            )
                            payload["status"] = "ok"
            except Exception as e:
                payload = {
                    "status": "error",
                    "lop": lop_text,
                    "upper_week": upper_week_hint,
                    "message": f"Worker tiến độ PPCT lỗi: {type(e).__name__}: {str(e)[:160]}",
                }
            finally:
                if bridge:
                    try:
                        bridge.disconnect()
                    except Exception:
                        pass

            def _apply():
                if request_id != self._sched_teacher_progress_request_id:
                    return
                self._render_sched_teacher_progress(payload)

            self._post_ui(_apply)

        threading.Thread(target=_work, daemon=True).start()

    def _collect_sched_teacher_progress_report(self, bridge, lop_text, upper_week, teacher_name, fast_mode=False):
        """Quét từ tuần mới về cũ để tìm môn giáo viên đang dạy trong lớp."""
        teacher_key = self._normalize_person_name(teacher_name)
        teacher_subjects = {}
        latest_by_subject = {}
        fast_mode = bool(fast_mode)

        upper_week_num = int(upper_week)
        week_numbers_desc = list(range(upper_week_num, 0, -1))
        requested_weeks = len(week_numbers_desc)

        week_errors = []
        weeks_scanned = 0
        cache_hits = 0
        bulk_hits = 0
        fallback_hits = 0
        requested_concurrency = 0
        effective_concurrency = 0
        scan_stopped_early = False

        # Quét theo batch để tận dụng cache/fetch bulk và cho phép dừng sớm an toàn.
        batch_size = 8
        requested_bulk_concurrency = 5 if fast_mode else 4
        if fast_mode:
            min_weeks_before_early_stop = 12
            stable_batches_needed = 1
        else:
            min_weeks_before_early_stop = 28
            stable_batches_needed = 3
        stable_batches_without_progress = 0

        for batch_start in range(0, len(week_numbers_desc), batch_size):
            week_batch = week_numbers_desc[batch_start:batch_start + batch_size]
            payload_map, batch_week_errors, fetch_meta = self._fetch_class_stats_week_payloads_bulk(
                bridge,
                lop_text,
                week_batch,
                concurrency=requested_bulk_concurrency,
            )
            week_errors.extend(batch_week_errors)
            cache_hits += int(fetch_meta.get("cache_hits", 0) or 0)
            bulk_hits += int(fetch_meta.get("bulk_hits", 0) or 0)
            fallback_hits += int(fetch_meta.get("fallback_hits", 0) or 0)
            requested_concurrency = max(
                requested_concurrency,
                int(fetch_meta.get("requested_concurrency", 0) or 0),
            )
            effective_concurrency = max(
                effective_concurrency,
                int(fetch_meta.get("effective_concurrency", 0) or 0),
            )

            batch_has_progress_update = False
            for tuan_num in week_batch:
                payload = payload_map.get(tuan_num)
                if payload is None:
                    continue

                weeks_scanned += 1
                rows = list((payload or {}).get("rows") or [])
                for row in rows:
                    if not row.get("has_data"):
                        continue
                    mon_hoc = str(row.get("mon_hoc", "")).strip()
                    if not mon_hoc:
                        continue
                    subject_key = self._normalize_class_stats_subject(mon_hoc)
                    ky_ten = str(row.get("ky_ten", "")).strip()
                    if ky_ten and self._normalize_person_name(ky_ten) == teacher_key:
                        teacher_subjects.setdefault(subject_key, mon_hoc)

                    if subject_key not in teacher_subjects:
                        continue

                    ppct_text = str(row.get("ppct", "")).strip()
                    match = re.search(r"\d+", ppct_text)
                    if not match:
                        continue

                    occurrence = {
                        "mon_hoc": teacher_subjects.get(subject_key, mon_hoc),
                        "ppct": int(match.group()),
                        "ppct_text": ppct_text,
                        "week": tuan_num,
                        "week_text": f"Tuần {tuan_num}",
                        "thu": str(row.get("thu", "")).strip(),
                        "buoi": str(row.get("buoi", "")).strip(),
                        "tiet": str(row.get("tiet", "")).strip(),
                        "slot_label": self._format_schedule_slot_label(
                            row.get("thu", "?"),
                            row.get("buoi", "?"),
                            row.get("tiet", "?"),
                        ),
                        "thu_full": str(row.get("thu_full", "")).strip(),
                        "noi_dung_cong_viec": str(row.get("noi_dung_cong_viec", "")).strip(),
                        "nhan_xet_giao_vien": str(row.get("nhan_xet_giao_vien", "")).strip(),
                        "ky_ten": str(row.get("ky_ten", "")).strip(),
                        "sort_key": self._schedule_occurrence_sort_key({
                            "week": tuan_num,
                            "thu": row.get("thu", ""),
                            "buoi": row.get("buoi", ""),
                            "tiet": row.get("tiet", ""),
                        }),
                    }
                    current = latest_by_subject.get(subject_key)
                    if current is None:
                        latest_by_subject[subject_key] = occurrence
                        batch_has_progress_update = True
                        continue
                    if occurrence["ppct"] > current["ppct"]:
                        latest_by_subject[subject_key] = occurrence
                        batch_has_progress_update = True
                        continue
                    if occurrence["ppct"] == current["ppct"] and occurrence["sort_key"] > current["sort_key"]:
                        latest_by_subject[subject_key] = occurrence
                        batch_has_progress_update = True

            if batch_has_progress_update:
                stable_batches_without_progress = 0
            elif latest_by_subject:
                stable_batches_without_progress += 1

            if (
                latest_by_subject
                and weeks_scanned >= min_weeks_before_early_stop
                and stable_batches_without_progress >= stable_batches_needed
            ):
                scan_stopped_early = True
                break

        subjects = self._canonicalize_sched_teacher_subjects(
            list(latest_by_subject.values())
        )
        return {
            "teacher_name": teacher_name,
            "lop": lop_text,
            "upper_week": upper_week_num,
            "subjects": subjects,
            "weeks_requested": requested_weeks,
            "weeks_scanned": weeks_scanned,
            "scan_stopped_early": scan_stopped_early,
            "fast_mode": fast_mode,
            "cache_hits": cache_hits,
            "bulk_hits": bulk_hits,
            "fallback_hits": fallback_hits,
            "requested_concurrency": requested_concurrency,
            "effective_concurrency": effective_concurrency,
            "week_errors": week_errors,
        }

    def _canonicalize_sched_teacher_subjects(self, subjects):
        """Gộp các biến thể base/detailed của cùng một môn khi chỉ có một phân môn thật sự."""
        items = list(subjects or [])
        if not items:
            return []

        groups = {}
        for item in items:
            mon_hoc = str(item.get("mon_hoc", "")).strip()
            base_text = re.sub(r"\s*\([^)]*\)\s*", " ", mon_hoc).strip() or mon_hoc
            base_key = self._normalize_class_stats_subject(base_text)
            full_key = self._normalize_class_stats_subject(mon_hoc)
            groups.setdefault(base_key, []).append((full_key, item))

        merged = []
        for base_key, entries in groups.items():
            plain_entries = [item for full_key, item in entries if full_key == base_key]
            detailed_entries = [item for full_key, item in entries if full_key != base_key]

            if plain_entries and len(detailed_entries) == 1:
                candidates = plain_entries + detailed_entries
                best_item = max(
                    candidates,
                    key=lambda item: (
                        int(item.get("ppct", 0) or 0),
                        item.get("sort_key", (0, 0, 0, 0)),
                    ),
                )
                merged_item = dict(best_item)
                merged_item["mon_hoc"] = str(detailed_entries[0].get("mon_hoc", "")).strip() or str(best_item.get("mon_hoc", "")).strip()
                merged.append(merged_item)
                continue

            merged.extend(item for _full_key, item in entries)

        return sorted(
            merged,
            key=lambda item: (-int(item.get("ppct", 0) or 0), str(item.get("mon_hoc", ""))),
        )

    def _ensure_bridge_detail_ready(self, bridge, action_name):
        """Xác nhận tab đang ở màn Chi tiết sổ đầu bài trước khi thao tác nghiệp vụ."""
        try:
            if bridge and bridge._is_v5_detail_ready():
                return True, ""
        except Exception:
            pass

        current_url = ""
        try:
            current_url = str(bridge.page.url or "")
        except Exception:
            current_url = ""
        return (
            False,
            f"{action_name}: tab hiện tại chưa ở màn Chi tiết sổ đầu bài. "
            f"Hãy dùng Đăng nhập và chuẩn bị trước. URL hiện tại: {current_url or '(không xác định)'}"
        )

    @staticmethod
    def _week_numbers_from_vars(var_from, var_to, *, require_multi_week=False):
        """Đọc khoảng tuần từ IntVar, trả về list tuần hợp lệ hoặc None nếu không đáng tin."""
        try:
            tuan_from = int(var_from.get())
            tuan_to = int(var_to.get())
        except Exception:
            return None
        if tuan_from > tuan_to:
            tuan_from, tuan_to = tuan_to, tuan_from
        if not (1 <= tuan_from <= 52 and 1 <= tuan_to <= 52):
            return None
        if require_multi_week and tuan_from == tuan_to:
            return None
        return list(range(tuan_from, tuan_to + 1))

    def _on_sched_load_lop(self):
        """Tải danh sách Lớp từ VnEdu → populate schedule combobox."""
        if not self._cdp_connected:
            return

        tuan_nums = self._week_numbers_from_vars(
            self.var_sched_tuan_from,
            self.var_sched_tuan_to,
        )

        scan_scope = (
            f"Tuần {tuan_nums[0]}→{tuan_nums[-1]}" if tuan_nums else "toàn bộ tuần có trên web"
        )
        self._log(f"📥 Đang tải DS Lớp cho Lịch dạy ({scan_scope})...", "info")

        def _work():
            try:
                bridge = ChromeBridge(port=self._cdp_port)
                ok, msg = bridge.connect()
                if ok:
                    ok_ready, ready_msg = self._ensure_bridge_detail_ready(
                        bridge,
                        "Tải DS Lớp",
                    )
                    if not ok_ready:
                        bridge.disconnect()
                        self._post_ui(lambda: self._log(ready_msg, "warning"))
                        return
                    ok2, lop_data = bridge.discover_lop_options_for_weeks(tuan_nums=tuan_nums)
                    bridge.disconnect()
                    if ok2:
                        options = self._sort_lop_options(lop_data.get("options", []))
                        weeks_scanned = len(lop_data.get("weeks_scanned", []) or [])
                        self._post_ui(lambda: self._populate_sched_lop(options))
                        self._post_ui(lambda: self._log(
                            f"Đã quét lớp theo {weeks_scanned} tuần ({scan_scope}), tìm thấy {len(options)} lớp.",
                            "success",
                        ))
                    else:
                        self._post_ui(lambda: self._log(f"Lỗi tải DS Lớp: {lop_data}", "error"))
                else:
                    self._post_ui(lambda: self._log(f"Lỗi kết nối: {msg}", "error"))
            except Exception as e:
                self._post_ui(lambda e=e: self._log(f"Exception tải DS: {e}", "error"))

        threading.Thread(target=_work, daemon=True).start()

    def _populate_sched_lop(self, options):
        """Populate schedule Lớp combobox.

        Args:
            options: list[str] — danh sách tên lớp
        """
        options = self._sort_lop_options(options)
        prev_lop = self.var_sched_lop.get().strip()
        self.cmb_sched_lop['values'] = options
        if prev_lop and prev_lop in options:
            self.cmb_sched_lop.set(prev_lop)
        elif options:
            self.cmb_sched_lop.set(options[0])
        self._log(f"Đã tải {len(options)} lớp cho Lịch dạy", "success")
        if self.var_sched_lop.get().strip():
            self.var_sched_teacher_progress_status.set(
                "Đã sẵn sàng. App sẽ prewarm nền tiến độ PPCT cho lớp đang chọn."
            )
        self._render_sched_teacher_progress(None)
        if self._cdp_connected and self.var_sched_lop.get().strip():
            self._schedule_sched_teacher_progress_prewarm(delay_ms=350)

    def _on_sched_scan_form(self):
        """Quét form popup VnEdu → đọc Phân môn dropdown options → populate GUI.

        Flow: CDP mở form tạm (click ➕ row 0) → đọc combobox store → đóng form.
        """
        if not self._cdp_connected:
            self._log("Chưa kết nối CDP!", "warning")
            return

        self._log("🔍 Đang quét form dropdown options...", "info")
        self.btn_sched_scan_form.config(state="disabled")

        def _work():
            current_info = None
            ok_info = False
            try:
                bridge = ChromeBridge(port=self._cdp_port)
                ok, msg = bridge.connect()
                if ok:
                    ok_ready, ready_msg = self._ensure_bridge_detail_ready(
                        bridge,
                        "Quét form",
                    )
                    if not ok_ready:
                        bridge.disconnect()
                        self._post_ui(lambda ready_msg=ready_msg: self._set_sched_form_rescan_reason(
                            f"{ready_msg} Hãy mở đúng màn sổ đầu bài của lớp hiện tại rồi bấm [Quét Form] lại."
                        ))
                        self._post_ui(lambda: self._log(ready_msg, "warning"))
                        return
                    ok_info, current_info = bridge.get_current_selection()
                    cached_data = self._get_sched_form_options_cache(current_info) if ok_info else None
                    if cached_data is not None:
                        bridge.disconnect()
                        self._post_ui(lambda: self._log("⚡ Quét form: dùng cache hiện có.", "info"))
                        self._post_ui(lambda: self._populate_sched_phan_mon(cached_data, current_info))
                        return

                    ok2, data = bridge.read_form_options(row_index=0)
                    bridge.disconnect()
                    if ok2:
                        if ok_info:
                            self._set_sched_form_options_cache(current_info, data)
                        self._post_ui(lambda: self._populate_sched_phan_mon(data, current_info if ok_info else None))
                    else:
                        self._post_ui(lambda: self._set_sched_form_rescan_reason(
                            "Quét Form chưa thành công. Hãy giữ đúng màn sổ đầu bài của lớp hiện tại rồi bấm [Quét Form] lại."
                        ))
                        self._post_ui(lambda: self._log(
                            f"❌ Quét form lỗi: {data}", "error"))
                else:
                    self._post_ui(lambda: self._set_sched_form_rescan_reason(
                        "Không kết nối được CDP để Quét Form. Hãy kiểm tra lại CDP rồi bấm [Quét Form] lại."
                    ))
                    self._post_ui(lambda: self._log(
                        f"❌ Kết nối CDP lỗi: {msg}", "error"))
            except Exception as e:
                self._post_ui(lambda: self._set_sched_form_rescan_reason(
                    "Quét Form bị gián đoạn. Hãy mở lại đúng màn sổ đầu bài rồi bấm [Quét Form] lại."
                ))
                self._post_ui(lambda e=e: self._log(
                    f"❌ Exception quét form: {e}", "error"))
            finally:
                self._post_ui(lambda: self.btn_sched_scan_form.config(
                    state="normal"))

        threading.Thread(target=_work, daemon=True).start()

    def _set_quick_prepare_button_state(self):
        """Đồng bộ trạng thái nút chuẩn bị nhanh."""
        if not hasattr(self, "btn_quick_prepare"):
            return
        enabled = self._cdp_connected and (not self._quick_prepare_running)
        self.btn_quick_prepare.config(state="normal" if enabled else "disabled")

    def _populate_sched_phan_mon(self, data, selection_info=None):
        """Populate Phân môn + Môn học combobox từ CDP scan data.

        Args:
            data: dict — {
                'phan_mon': [{value, text}, ...],
                'phan_mon_by_mon_hoc': {mon_hoc_value: [{value, text}, ...]},
                'xep_loai': [...],
                'mon_hoc': [{value, text}, ...],
                'mon_hoc_field': str (tên field trên VnEdu)
            }
        """
        prev_phan_mon = self.var_sched_phan_mon.get()
        prev_mon_hoc = self.var_sched_mon_hoc.get()

        # --- Cache options từ popup ---
        self._sched_phan_mon_options = list(data.get("phan_mon", []) or [])
        raw_pm_map = data.get("phan_mon_by_mon_hoc", {}) or {}
        self._sched_phan_mon_by_mon_hoc = {
            str(key): list(options or [])
            for key, options in raw_pm_map.items()
            if key is not None
        }

        mh_options = list(data.get("mon_hoc", []) or [])
        self._sched_mon_hoc_options = mh_options
        self._sched_mon_hoc_field = data.get("mon_hoc_field")
        self._mark_sched_form_scan_ready(selection_info)

        # --- Populate Môn học, ưu tiên giữ selection cũ nếu còn hợp lệ ---
        selected_mon_hoc = self._set_sched_combobox_selection(
            self.cmb_sched_mon_hoc,
            self.var_sched_mon_hoc,
            mh_options,
            preferred_text=prev_mon_hoc,
        )

        # --- Populate Phân môn theo Môn học đang chọn ---
        active_pm_options = self._refresh_sched_phan_mon_values(
            preferred_text=prev_phan_mon
        )

        self._log(
            f"✅ Đã tải {len(mh_options)} Môn học, "
            f"{len(active_pm_options)} Phân môn cho '{selected_mon_hoc or '(trống)'}'"
            + (" (đã map theo Môn học)" if self._sched_phan_mon_by_mon_hoc else ""),
            "success"
        )
        missing_map_subjects = [
            str(opt.get("text", "")).strip()
            for opt in mh_options
            if str(opt.get("text", "")).strip()
            and not self._select_sched_phan_mon_options(
                opt.get("value"),
                self._sched_phan_mon_by_mon_hoc,
                [],
            )
        ]
        if missing_map_subjects:
            self._log(
                "⚠ Một số Môn học chưa tải được Phân môn chuyên biệt: "
                + ", ".join(missing_map_subjects[:6])
                + ". Nếu bạn vừa đổi lớp hoặc đổi màn VnEdu, hãy bấm [Quét Form] lại.",
                "warning",
            )
        if selected_mon_hoc and not active_pm_options:
            self._log(
                f"⚠ Môn học '{selected_mon_hoc}' hiện chưa có Phân môn hợp lệ. "
                "Nút chạy sẽ bị khóa cho tới khi bạn bấm [Quét Form] lại hoặc quay về đúng màn VnEdu.",
                "warning",
            )
        self._set_schedule_button_states(
            running=self._schedule_running,
            can_resume=bool(self._schedule_resume_state),
        )

    @staticmethod
    def _make_sched_form_options_cache_key(selection_info):
        """Sinh cache key cho dữ liệu quét form theo ngữ cảnh lớp/cấp hiện tại."""
        info = selection_info or {}
        cap = str(info.get("cap", "")).strip().lower()
        lop = str(info.get("lop", "")).strip().lower()
        if not cap and not lop:
            return None
        return (cap, lop)

    def _get_sched_form_options_cache(self, selection_info):
        """Đọc cache quét form theo lớp/cấp hiện tại."""
        cache_key = self._make_sched_form_options_cache_key(selection_info)
        if cache_key is None:
            return None
        with self._sched_form_options_cache_lock:
            cached = self._sched_form_options_cache.get(cache_key)
        return copy.deepcopy(cached) if cached is not None else None

    def _set_sched_form_options_cache(self, selection_info, data):
        """Lưu cache quét form theo lớp/cấp hiện tại."""
        cache_key = self._make_sched_form_options_cache_key(selection_info)
        if cache_key is None:
            return
        with self._sched_form_options_cache_lock:
            self._sched_form_options_cache[cache_key] = copy.deepcopy(data)

    def _clear_sched_form_options_cache(self):
        """Xóa cache dữ liệu quét form trong session hiện tại."""
        with self._sched_form_options_cache_lock:
            self._sched_form_options_cache.clear()

    @staticmethod
    def _normalize_sched_option_text(text):
        """Chuẩn hóa text option để so khớp ổn định."""
        return str(text or "").strip()

    def _resolve_sched_option(self, options, display_text):
        """Tìm option theo text hiển thị đã chọn trên combobox."""
        target = self._normalize_sched_option_text(display_text)
        if not target:
            return None

        for opt in options or []:
            if self._normalize_sched_option_text(opt.get("text")) == target:
                return opt
        return None

    def _get_sched_phan_mon_options_for_mon(self, mon_hoc_value=None):
        """Lấy danh sách Phân môn tương ứng với Môn học hiện tại."""
        target_value = mon_hoc_value
        if target_value is None:
            mon_opt = self._resolve_sched_option(
                self._sched_mon_hoc_options,
                self.var_sched_mon_hoc.get(),
            )
            if mon_opt is not None:
                target_value = mon_opt.get("value")

        return self._select_sched_phan_mon_options(
            target_value,
            self._sched_phan_mon_by_mon_hoc,
            self._sched_phan_mon_options,
        )

    def _set_sched_combobox_selection(self, combobox, variable, options,
                                      preferred_text=""):
        """Populate combobox từ options và giữ selection cũ nếu còn hợp lệ."""
        display_list = [
            opt.get("text", "")
            for opt in options or []
            if self._normalize_sched_option_text(opt.get("text"))
        ]
        combobox["values"] = display_list

        selected_text = ""
        preferred_opt = self._resolve_sched_option(options, preferred_text)
        if preferred_opt is not None:
            selected_text = preferred_opt.get("text", "")
        elif display_list:
            selected_text = display_list[0]

        variable.set(selected_text)
        combobox.set(selected_text)
        return selected_text

    def _refresh_sched_phan_mon_values(self, preferred_text=None):
        """Đồng bộ combobox Phân môn theo Môn học đang chọn."""
        pm_options = self._get_sched_phan_mon_options_for_mon()
        selected_text = preferred_text
        if selected_text is None:
            selected_text = self.var_sched_phan_mon.get()

        self._set_sched_combobox_selection(
            self.cmb_sched_phan_mon,
            self.var_sched_phan_mon,
            pm_options,
            preferred_text=selected_text,
        )
        if hasattr(self, "btn_sched_run"):
            self._set_schedule_button_states(
                running=self._schedule_running,
                can_resume=bool(self._schedule_resume_state),
            )
        return pm_options

    def _on_sched_mon_hoc_selected(self, _event=None):
        """Đổi Môn học → cập nhật lại danh sách Phân môn tương ứng."""
        self._refresh_sched_phan_mon_values()

    def _on_sched_phan_mon_selected(self, _event=None):
        """Đổi Phân môn → cập nhật lại state chạy/resume và guidance trên panel."""
        self._set_schedule_button_states(
            running=self._schedule_running,
            can_resume=bool(self._schedule_resume_state),
        )

    def _get_schedule_slots(self):
        """Trích xuất danh sách (thu, buoi, tiet) từ grid checkboxes.

        Returns:
            list[dict] — mỗi dict: {thu: int, buoi: str, tiet: int}
            Sắp xếp theo thu → buoi → tiet.
        """
        slots = []
        for thu in SCHEDULE_DAYS:
            buoi_text = self._sched_buoi[thu].get()
            if buoi_text == "---":
                continue

            # Xác định buổi cần quét
            if buoi_text == "Sáng":
                buoi_list = [("S", "Sáng")]
            elif buoi_text == "Chiều":
                buoi_list = [("C", "Chiều")]
            elif buoi_text == "Cả hai":
                # "Cả hai": checkboxes bind vào grid "S", tạo slots cho cả Sáng và Chiều
                buoi_list = [("S", "Sáng"), ("S", "Chiều")]
            else:
                continue

            for buoi_code, buoi_name in buoi_list:
                for tiet_idx in range(5):
                    var = self._sched_grid[(thu, buoi_code)][tiet_idx]
                    if var.get():
                        slots.append({
                            "thu": thu,
                            "buoi": buoi_name,
                            "tiet": tiet_idx + 1
                        })

        # Sắp xếp: thu → buoi (Sáng trước Chiều) → tiet
        slots.sort(key=lambda s: (s["thu"], 0 if s["buoi"] == "Sáng" else 1, s["tiet"]))
        return slots

    def _format_schedule_slot_label(self, thu, buoi, tiet):
        """Format nhãn slot chuẩn để dùng cho log và bảng tổng kết."""
        thu_token = ChromeBridge._normalize_thu_token(thu)
        if thu_token == "CN":
            return f"Chủ nhật {buoi} Tiết {tiet}"
        thu_display = thu_token or str(thu)
        return f"Thứ {thu_display} {buoi} Tiết {tiet}"

    @staticmethod
    def _format_sched_live_progress_text(current, total, phase, detail="", state="idle"):
        """Tạo text tiến trình gọn, dễ đọc cho thanh live progress."""
        icons = {
            "idle": "●",
            "running": "🟢",
            "paused": "⏸",
            "success": "✅",
            "error": "✗",
        }
        safe_phase = str(phase or "Tiến trình").strip() or "Tiến trình"
        safe_detail = str(detail or "").strip()
        total_value = max(int(total or 0), 0)
        current_value = max(int(current or 0), 0)

        if total_value > 0:
            current_value = min(current_value, total_value)
            percent = int(round((current_value / total_value) * 100))
            text = f"{icons.get(state, '●')} {safe_phase}: {current_value}/{total_value} ({percent}%)"
            if state == "running" and current_value < total_value:
                text += f" | còn {total_value - current_value} bước"
        else:
            text = f"{icons.get(state, '●')} {safe_phase}"

        if safe_detail:
            text += f" | {safe_detail}"
        return text

    def _set_sched_live_progress(self, current=None, total=None, phase="Tiến trình", detail="", state="idle"):
        """Đồng bộ thanh progress xanh + text live để người dùng biết app còn đang chạy."""
        if not hasattr(self, "var_sched_live_progress"):
            return

        progressbars = []
        if hasattr(self, "cdp_live_progressbar"):
            progressbars.append(self.cdp_live_progressbar)
        if hasattr(self, "sched_progressbar"):
            progressbars.append(self.sched_progressbar)
        if hasattr(self, "compact_progressbar"):
            progressbars.append(self.compact_progressbar)

        if progressbars:
            maximum = total
            if maximum is None:
                try:
                    maximum = int(float(progressbars[0]["maximum"]))
                except Exception:
                    maximum = 0
            maximum = max(int(maximum or 0), 1)
            value = current
            if value is None:
                try:
                    value = int(float(progressbars[0]["value"]))
                except Exception:
                    value = 0
            value = max(0, min(int(value or 0), maximum))
            for progressbar in progressbars:
                progressbar["maximum"] = maximum
                progressbar["value"] = value
        else:
            maximum = max(int(total or 0), 0)
            value = max(int(current or 0), 0)

        self.var_sched_live_progress.set(
            self._format_sched_live_progress_text(
                current=value,
                total=maximum,
                phase=phase,
                detail=detail,
                state=state,
            )
        )

    def _build_schedule_request(self, show_messages=True):
        """Thu thập dữ liệu schedule ở mức request thô, chưa cần resolve ID từ CDP."""
        schedule_mode = self._get_sched_mode()
        lop_list = self._get_sched_lop_list(schedule_mode)
        sched_lop = lop_list[0] if lop_list else ""
        if not sched_lop:
            if show_messages:
                self._log("Chưa chọn Lớp!", "warning")
                messagebox.showwarning("Cảnh báo", "Chưa chọn Lớp trong panel Lịch dạy.")
            return None

        try:
            tuan_from = self.var_sched_tuan_from.get()
            tuan_to = self.var_sched_tuan_to.get()
        except (tk.TclError, ValueError):
            if show_messages:
                self._log("Tuần không hợp lệ!", "error")
            return None
        if tuan_from > tuan_to:
            tuan_from, tuan_to = tuan_to, tuan_from

        if schedule_mode == SCHEDULE_MODE_KHDH:
            slots = []
            mon_hoc_text = ""
            phan_mon_text = ""
        else:
            slots = self._get_schedule_slots()
            if not slots:
                if show_messages:
                    self._log("Chưa cấu hình Lịch dạy! Check ít nhất 1 tiết.", "warning")
                    messagebox.showwarning(
                        "Cảnh báo",
                        "Chưa cấu hình tiết nào trong Lịch dạy.\nHãy chọn Buổi và check các tiết cần nhập."
                    )
                return None

            mon_hoc_text = self.var_sched_mon_hoc.get().strip()
            phan_mon_text = self.var_sched_phan_mon.get().strip()
            if not mon_hoc_text or not phan_mon_text:
                if show_messages:
                    self._show_sched_form_blocked_warning(
                        "Chưa sẵn sàng chạy",
                        self._get_sched_form_invalid_reason()
                        or (
                            "Chưa chọn đủ Môn học / Phân môn.\n"
                            "Hãy chọn lại dữ liệu. Nếu danh sách đang lệch hoặc bị trống, hãy bấm [Quét Form] lại."
                        ),
                    )
                return None

        try:
            ppct_start = int(self.var_sched_ppct_start.get())
        except (tk.TclError, ValueError):
            ppct_start = 1

        return {
            "port": self._cdp_port,
            "tuan_from": int(tuan_from),
            "tuan_to": int(tuan_to),
            "lop": sched_lop,
            "lop_list": lop_list,
            "slots": slots,
            "hs_nghi": self.var_sched_hs_nghi.get() or "0",
            "diem": self.var_sched_diem.get() or "10",
            "nhan_xet_raw": self.var_sched_nhan_xet.get() or "Lớp học chăm ngoan",
            "ppct_start": int(ppct_start),
            "mon_hoc_text": mon_hoc_text,
            "phan_mon_text": phan_mon_text,
            "schedule_mode": schedule_mode,
            "username": self.var_vnedu_username.get().strip(),
            "password": self.var_vnedu_password.get(),
        }

    def _show_auto_confirm_dialog(self, request_data):
        """Hiển thị dialog xác nhận auto-login + nhập dữ liệu với màu nhấn mạnh."""
        accepted = {"value": False}
        khdh_mode = str(
            request_data.get("schedule_mode", SCHEDULE_MODE_MANUAL) or SCHEDULE_MODE_MANUAL
        ).strip().lower() == SCHEDULE_MODE_KHDH
        slot_lines = []
        if khdh_mode:
            slot_lines.append("Mode KHDH: quét các row chữ đỏ theo gợi ý live của từng tuần")
        else:
            slot_lines = [
                self._format_schedule_slot_label(
                    slot.get("thu", "?"),
                    slot.get("buoi", "?"),
                    slot.get("tiet", "?"),
                )
                for slot in (request_data.get("slots") or [])
            ]
        win = tk.Toplevel(self.root)
        win.title("Xác nhận Auto Đăng nhập & Nhập dữ liệu")
        win.transient(self.root)
        win.grab_set()
        win.resizable(False, False)

        ttk.Label(
            win,
            text="Kiểm tra kỹ trước khi chạy tự động hoàn toàn",
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", padx=12, pady=(10, 6))

        txt = tk.Text(win, width=68, height=15, wrap="word", font=("Segoe UI", 9))
        txt.pack(fill="both", expand=True, padx=12)
        txt.tag_configure("section", foreground="#0b5394", font=("Segoe UI", 9, "bold"))
        txt.tag_configure("subject", foreground="#38761d")
        txt.tag_configure("time", foreground="#b45f06")
        txt.tag_configure("slot", foreground="#674ea7")
        txt.tag_configure("warn", foreground="#cc0000", font=("Segoe UI", 9, "bold"))

        txt.insert("end", "Môn học / Phân môn\n", "section")
        if khdh_mode:
            txt.insert("end", "Theo dữ liệu gợi ý KHDH live từ từng row đỏ\n\n", "subject")
        else:
            txt.insert("end", f"Môn học: {request_data.get('mon_hoc_text', '')}\n", "subject")
            txt.insert("end", f"Phân môn: {request_data.get('phan_mon_text', '')}\n\n", "subject")
        txt.insert("end", "Thời gian / Lớp / PPCT\n", "section")
        txt.insert(
            "end",
            f"Từ tuần {request_data.get('tuan_from')} đến tuần {request_data.get('tuan_to')}\n",
            "time",
        )
        txt.insert("end", f"Lớp: {request_data.get('lop')}\n", "time")
        if khdh_mode:
            txt.insert("end", "PPCT: lấy trực tiếp từ từng row gợi ý KHDH\n\n", "time")
        else:
            txt.insert("end", f"PPCT bắt đầu: {request_data.get('ppct_start')}\n\n", "time")
        txt.insert("end", "Các slot sẽ nhập\n", "section")
        for line in slot_lines:
            txt.insert("end", f"• {line}\n", "slot")
        txt.insert("end", "\n")

        warnings = []
        if not request_data.get("username"):
            warnings.append("Thiếu tài khoản VnEdu")
        if not request_data.get("password"):
            warnings.append("Thiếu mật khẩu VnEdu")
        if warnings:
            txt.insert("end", "Cảnh báo\n", "section")
            for item in warnings:
                txt.insert("end", f"• {item}\n", "warn")

        txt.config(state="disabled")

        row = ttk.Frame(win)
        row.pack(fill="x", padx=12, pady=10)

        def _accept():
            accepted["value"] = True
            win.destroy()

        ttk.Button(row, text="Bắt đầu", style="QuickGreen.TButton", command=_accept).pack(side="right", padx=(6, 0))
        ttk.Button(row, text="Hủy", command=win.destroy).pack(side="right")

        try:
            self.root.wait_window(win)
        except Exception:
            pass
        return accepted["value"]

    def _update_sched_ppct_runtime(self, last_success=None, next_ppct=None, status="idle"):
        """Đồng bộ trạng thái PPCT runtime lên GUI.

        `var_sched_ppct_start` hiển thị tiết PPCT vừa nhập thành công gần nhất,
        còn `next_ppct` được giữ riêng cho resume để tránh trùng PPCT.
        """
        self._schedule_last_success_ppct = last_success
        self._schedule_next_ppct = next_ppct

        if last_success is not None:
            try:
                self.var_sched_ppct_start.set(int(last_success))
            except Exception:
                pass

        status_map = {
            "idle": "Sẵn sàng",
            "running": "Đang chạy",
            "paused": "Tạm dừng",
            "done": "Hoàn tất",
        }
        status_text = status_map.get(status, "Sẵn sàng")
        if self._is_sched_khdh_mode():
            last_text = "--" if last_success in (None, "") else str(last_success)
            self.var_sched_ppct_runtime.set(
                f"{status_text} | Mode KHDH | PPCT gần nhất từ popup: {last_text}"
            )
            return
        last_text = "--" if last_success is None else str(last_success)
        next_text = "--" if next_ppct is None else str(next_ppct)
        self.var_sched_ppct_runtime.set(
            f"{status_text} | PPCT vừa nhập: {last_text} | Kế tiếp nội bộ: {next_text}"
        )

    def _set_schedule_button_states(self, running=False, can_resume=False):
        """Đồng bộ trạng thái Run/Stop/Resume theo state schedule hiện tại."""
        current_mode = self._get_sched_mode()
        if current_mode == SCHEDULE_MODE_KHDH:
            subject_error = self._get_sched_mode_invalid_reason(current_mode)
            form_ready = not subject_error
            selection_ready = form_ready
        else:
            form_ready = self._has_valid_sched_form_scan()
            subject_selection = None
            subject_error = ""
            if form_ready:
                subject_selection, subject_error = self._resolve_current_sched_subject_selection()
            selection_ready = subject_selection is not None
        resume_ready = form_ready
        resume_selection_ready = selection_ready
        if can_resume and self._schedule_resume_params:
            resume_mode = str(
                self._schedule_resume_params.get("schedule_mode", SCHEDULE_MODE_MANUAL)
                or SCHEDULE_MODE_MANUAL
            ).strip().lower()
            if resume_mode == SCHEDULE_MODE_KHDH:
                resume_error = self._get_sched_mode_invalid_reason(SCHEDULE_MODE_KHDH)
                resume_ready = not resume_error
                resume_selection_ready = resume_ready
            else:
                resume_ready = self._has_valid_sched_form_scan()
                resume_subject_selection = None
                if resume_ready:
                    resume_subject_selection, _resume_error = self._resolve_current_sched_subject_selection()
                    resume_selection_ready = resume_subject_selection is not None
                else:
                    resume_selection_ready = False
        self.btn_sched_run.config(
            state="disabled" if running or not self._cdp_connected or not form_ready or not selection_ready else "normal"
        )
        self.btn_sched_stop.config(state="normal" if running else "disabled")
        self.btn_sched_resume.config(
            state="normal"
            if (
                can_resume and not running and self._cdp_connected
                and resume_ready and resume_selection_ready
            )
            else "disabled"
        )
        self._refresh_sched_form_guidance(subject_error=subject_error)

    def _set_auto_login_button_state(self):
        """Đồng bộ trạng thái nút auto-login + nhập dữ liệu."""
        if not hasattr(self, "btn_auto_login_run"):
            return
        enabled = (
            (not self._auto_login_running)
            and (not self._schedule_running)
            and not (self._class_stats_thread and self._class_stats_thread.is_alive())
        )
        self.btn_auto_login_run.config(state="normal" if enabled else "disabled")

    def _root_exists(self):
        """Kiểm tra root window còn sống để tránh callback ghi vào Tcl đã đóng."""
        try:
            return bool(self.root.winfo_exists())
        except Exception:
            return False

    def _post_ui(self, callback):
        """Đưa callback về UI thread an toàn qua queue nội bộ."""
        if callback is None or self._closing:
            return
        if threading.current_thread() is threading.main_thread():
            if self._root_exists():
                callback()
            return
        self._ui_task_queue.put(callback)

    def _pump_ui_tasks(self):
        """Thực thi các callback UI được worker gửi về; chỉ chạy trên main thread."""
        self._ui_task_pump_after_id = None
        if self._closing or not self._root_exists():
            return
        try:
            while not self._ui_task_queue.empty():
                callback = self._ui_task_queue.get_nowait()
                if self._closing or not self._root_exists():
                    continue
                try:
                    callback()
                except Exception as e:
                    print(f"[UI_TASK] Error: {e}")
        finally:
            if not self._closing and self._root_exists():
                self._ui_task_pump_after_id = self.root.after(
                    UI_TASK_POLL_MS,
                    self._pump_ui_tasks,
                )

    def _write_json_atomic(self, file_path, payload):
        """Ghi JSON theo kiểu temp-file + os.replace để tránh file nửa chừng."""
        temp_path = f"{file_path}.tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as tmp_file:
                json.dump(payload, tmp_file, ensure_ascii=False, indent=2)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())
            os.replace(temp_path, file_path)
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    def _schedule_resume_file_path(self):
        """Đường dẫn sidecar lưu checkpoint schedule để resume sau khi mở lại app."""
        return os.path.join(os.path.dirname(__file__), SCHEDULE_RESUME_FILE)

    @staticmethod
    def _build_initial_schedule_resume_state(params):
        """Sinh checkpoint ban đầu cho một phiên schedule mới."""
        schedule_mode = str(params.get("schedule_mode", SCHEDULE_MODE_MANUAL) or SCHEDULE_MODE_MANUAL)
        next_ppct = None
        if schedule_mode != SCHEDULE_MODE_KHDH:
            next_ppct = int(params.get("ppct_start", 1) or 1)
        return {
            "next_lop_idx": 0,
            "next_tuan_num": int(params.get("tuan_from", 1) or 1),
            "next_slot_idx": 0,
            "next_row_key": None,
            "next_ppct": next_ppct,
            "completed": 0,
            "skipped": 0,
            "errors": 0,
            "last_success_ppct": None,
        }

    def _persist_schedule_resume_snapshot(self):
        """Persist resume params/state xuống đĩa để survive app close/crash."""
        file_path = self._schedule_resume_file_path()
        if not self._schedule_resume_params or not self._schedule_resume_state:
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except OSError:
                    pass
            self._schedule_resume_dirty = False
            return

        payload = {
            "version": 1,
            "saved_at": time.time(),
            "params": copy.deepcopy(self._schedule_resume_params),
            "resume_state": copy.deepcopy(self._schedule_resume_state),
        }
        self._write_json_atomic(file_path, payload)
        self._schedule_resume_dirty = False

    def _update_schedule_resume_snapshot(self, resume_state=None, params=None, persist=True):
        """Cập nhật checkpoint đang giữ trong RAM và ghi xuống đĩa nếu cần."""
        if params is not None:
            self._schedule_resume_params = copy.deepcopy(params) if params else None
        self._schedule_resume_state = copy.deepcopy(resume_state) if resume_state else None
        self._schedule_resume_dirty = True
        if persist:
            self._persist_schedule_resume_snapshot()

    def _load_schedule_resume_snapshot(self):
        """Nạp checkpoint schedule còn dang dở từ sidecar JSON."""
        file_path = self._schedule_resume_file_path()
        if not os.path.exists(file_path):
            self._update_schedule_resume_snapshot(None, params=None, persist=False)
            self._schedule_resume_dirty = False
            return
        try:
            with open(file_path, "r", encoding="utf-8") as resume_file:
                raw = json.load(resume_file)
            params = raw.get("params")
            resume_state = raw.get("resume_state")
            if not isinstance(params, dict) or not isinstance(resume_state, dict):
                raise ValueError("Resume snapshot không hợp lệ")
            self._update_schedule_resume_snapshot(
                resume_state=resume_state,
                params=params,
                persist=False,
            )
            self._schedule_resume_dirty = False
        except Exception as e:
            self._log(f"Lỗi load checkpoint schedule: {e}", "warning")
            self._update_schedule_resume_snapshot(None, params=None, persist=False)
            self._schedule_resume_dirty = False

    def _reset_sched_form_state(self, clear_cache=True):
        """Xóa toàn bộ state quét form để tránh dùng option stale giữa các session."""
        self._sched_phan_mon_options = []
        self._sched_phan_mon_by_mon_hoc = {}
        self._sched_mon_hoc_options = []
        self._sched_mon_hoc_field = None
        self._sched_form_scan_session_id = None
        self._sched_form_scan_context_key = None
        if clear_cache:
            self._clear_sched_form_options_cache()
        self.var_sched_mon_hoc.set("")
        self.var_sched_phan_mon.set("")
        if hasattr(self, "cmb_sched_mon_hoc"):
            self.cmb_sched_mon_hoc["values"] = ()
            self.cmb_sched_mon_hoc.set("")
        if hasattr(self, "cmb_sched_phan_mon"):
            self.cmb_sched_phan_mon["values"] = ()
            self.cmb_sched_phan_mon.set("")

    def _mark_sched_form_session_changed(self, clear_cache=True, reason="", announce=False, log_level="warning"):
        """Đổi session-id cho form scan mỗi khi CDP context thay đổi."""
        self._sched_form_session_id += 1
        self._reset_sched_form_state(clear_cache=clear_cache)
        self._set_sched_form_rescan_reason(
            reason,
            announce=announce,
            log_level=log_level,
        )

    def _mark_sched_form_scan_ready(self, selection_info=None):
        """Đánh dấu dữ liệu form hiện tại đã được quét thành công trong session CDP này."""
        self._sched_form_scan_session_id = self._sched_form_session_id
        self._sched_form_scan_context_key = self._make_sched_form_options_cache_key(selection_info)
        self._sched_form_rescan_reason = ""
        self._sched_form_last_announced_issue = ""

    def _current_sched_form_context_lop(self):
        """Đọc lớp đang chọn trên panel schedule để validate dữ liệu form đã quét."""
        try:
            return str(self.var_sched_lop.get() or "").strip().lower()
        except Exception:
            return ""

    def _scanned_sched_form_context_lop(self):
        """Đọc lớp đã gắn với lần Quét Form gần nhất."""
        if isinstance(self._sched_form_scan_context_key, (tuple, list)) and len(self._sched_form_scan_context_key) >= 2:
            return str(self._sched_form_scan_context_key[1] or "").strip().lower()
        return ""

    def _set_sched_form_rescan_reason(self, reason="", announce=False, log_level="warning"):
        """Lưu lý do cần Quét Form lại và đồng bộ lại guidance trên panel."""
        reason = str(reason or "").strip()
        changed = reason != self._sched_form_rescan_reason
        self._sched_form_rescan_reason = reason
        if not reason:
            self._sched_form_last_announced_issue = ""
        if announce and reason and changed:
            self._sched_form_last_announced_issue = reason
            self._log(f"⚠ {reason}", log_level)
        if hasattr(self, "btn_sched_run"):
            self._set_schedule_button_states(
                running=self._schedule_running,
                can_resume=bool(self._schedule_resume_state),
            )
        else:
            self._refresh_sched_form_guidance()

    def _get_sched_form_invalid_reason(self):
        """Tạo thông điệp hành động rõ ràng khi dữ liệu form hiện tại không còn dùng được."""
        if self._sched_form_rescan_reason:
            return self._sched_form_rescan_reason

        current_lop_display = str(self.var_sched_lop.get() or "").strip()
        current_lop = current_lop_display.lower()
        scanned_lop = self._scanned_sched_form_context_lop()

        if not self._cdp_connected:
            return "Chưa kết nối CDP. Hãy kết nối lại rồi bấm [Quét Form] trước khi chạy schedule."
        if not self._sched_mon_hoc_options or not self._sched_phan_mon_by_mon_hoc:
            if current_lop_display:
                return (
                    f"Chưa có dữ liệu Môn học / Phân môn hợp lệ cho lớp '{current_lop_display}'. "
                    "Hãy bấm [Quét Form] trước khi chạy schedule."
                )
            return "Chưa có dữ liệu Môn học / Phân môn hợp lệ. Hãy bấm [Quét Form] trước khi chạy schedule."
        if self._sched_form_scan_session_id != self._sched_form_session_id:
            return "Session CDP đã thay đổi sau lần Quét Form trước. Hãy bấm [Quét Form] lại."
        if current_lop and scanned_lop and current_lop != scanned_lop:
            return (
                f"Bạn đã đổi Lớp từ '{scanned_lop.upper()}' sang '{current_lop_display}'. "
                "Hãy bấm [Quét Form] lại trước khi chạy schedule."
            )
        if current_lop and not scanned_lop:
            return (
                f"Không xác định được lớp của lần Quét Form trước trong khi bạn đang chọn "
                f"'{current_lop_display}'. Hãy bấm [Quét Form] lại."
            )
        return ""

    def _refresh_sched_form_guidance(self, subject_error=""):
        """Hiển thị guidance ngắn ngay trên panel để người dùng biết khi nào phải Quét Form lại."""
        if not hasattr(self, "lbl_sched_progress") or self._schedule_running:
            return

        if self._is_sched_khdh_mode():
            lop_list = self._get_sched_lop_list(SCHEDULE_MODE_KHDH)
            if len(lop_list) <= 1:
                lop_text = lop_list[0] if lop_list else "(chưa chọn lớp)"
            else:
                lop_text = f"{len(lop_list)} lớp: " + ", ".join(lop_list[:4])
                if len(lop_list) > 4:
                    lop_text += f"... (+{len(lop_list) - 4})"
            issue = self._get_sched_mode_invalid_reason(SCHEDULE_MODE_KHDH)
            if issue:
                self.lbl_sched_progress.config(text=f"⚠ {issue}")
                return
            self.lbl_sched_progress.config(
                text=(
                    f"✅ Mode KHDH: Lớp {lop_text}. App sẽ bỏ qua grid tiết + Môn/Phân môn/PPCT nhập tay, "
                    "bật Gợi ý theo KHDH live rồi chỉ nhập HS nghỉ / Nhận xét / Điểm vào các row chữ đỏ."
                )
            )
            return

        issue = self._get_sched_form_invalid_reason()
        if not issue and subject_error:
            issue = subject_error
        if issue:
            compact_issue = " ".join(str(issue).split())
            self.lbl_sched_progress.config(text=f"⚠ {compact_issue}")
            return

        self._sched_form_last_announced_issue = ""

        lop_text = str(self.var_sched_lop.get() or "").strip() or "(chưa chọn lớp)"
        mon_hoc_text = str(self.var_sched_mon_hoc.get() or "").strip() or "(chưa chọn môn học)"
        phan_mon_text = str(self.var_sched_phan_mon.get() or "").strip() or "(chưa chọn phân môn)"
        self.lbl_sched_progress.config(
            text=(
                f"✅ Đã sẵn sàng: Lớp {lop_text} | {mon_hoc_text} / {phan_mon_text}. "
                "Nếu đổi Lớp, đổi màn VnEdu hoặc reconnect CDP, hãy bấm [Quét Form] lại."
            )
        )

    def _has_valid_sched_form_scan(self):
        """Kiểm tra cache form hiện tại còn hợp lệ cho session CDP đang dùng hay không."""
        return not self._get_sched_form_invalid_reason()

    @staticmethod
    def _select_sched_phan_mon_options(mon_hoc_value, phan_mon_by_mon_hoc, phan_mon_options_all):
        """Lấy đúng danh sách Phân môn cho một Môn học; fail-closed nếu map bị thiếu."""
        if mon_hoc_value is None:
            return list(phan_mon_options_all or [])
        key = str(mon_hoc_value).strip()
        if not key:
            return list(phan_mon_options_all or [])
        if key in (phan_mon_by_mon_hoc or {}):
            return list((phan_mon_by_mon_hoc or {}).get(key) or [])
        return []

    @staticmethod
    def _extract_existing_ppct_value(row):
        """Parse số PPCT hiện có trên row để quyết định có consume progression hay không."""
        text = str((row or {}).get("ppct", "") or "").strip()
        match = re.search(r"\d+", text)
        if not match:
            return None
        try:
            return int(match.group())
        except Exception:
            return None

    def _resolve_current_sched_subject_selection(self):
        """Resolve subject/sub-subject IDs từ form scan hiện tại; fail-closed nếu state stale."""
        invalid_reason = self._get_sched_form_invalid_reason()
        if invalid_reason:
            return None, invalid_reason

        mon_hoc_display = self.var_sched_mon_hoc.get()
        mon_hoc_option = self._resolve_sched_option(
            self._sched_mon_hoc_options,
            mon_hoc_display,
        )
        if mon_hoc_option is None:
            return None, (
                "Không resolve được Môn học đã chọn từ cache CDP hiện tại.\n"
                "Có thể bạn vừa đổi lớp hoặc đổi màn VnEdu. Hãy bấm [Quét Form] lại rồi chọn lại Môn học."
            )
        mon_hoc_value = mon_hoc_option["value"]

        phan_mon_display = self.var_sched_phan_mon.get()
        phan_mon_options = self._get_sched_phan_mon_options_for_mon(mon_hoc_value)
        if not phan_mon_options:
            return None, (
                f"Không tải được danh sách Phân môn chuyên biệt cho Môn học "
                f"'{mon_hoc_display}'. Hãy bấm [Quét Form] lại trên đúng lớp và đúng màn VnEdu."
            )

        phan_mon_option = self._resolve_sched_option(
            phan_mon_options,
            phan_mon_display,
        )
        if phan_mon_option is None:
            return None, (
                "Phân môn hiện tại không thuộc Môn học đã chọn trong session CDP này.\n"
                "Hãy chọn lại Phân môn. Nếu bạn vừa đổi lớp, đổi màn VnEdu hoặc reconnect CDP, hãy bấm [Quét Form] lại."
            )

        return {
            "mon_hoc_value": mon_hoc_value,
            "mon_hoc_text": mon_hoc_option.get("text", ""),
            "phan_mon_value": phan_mon_option.get("value"),
            "phan_mon_text": phan_mon_option.get("text", ""),
            "mon_hoc_field": self._sched_mon_hoc_field,
        }, ""

    def _show_sched_form_blocked_warning(self, title, message):
        """Hiển thị thông báo block với hướng dẫn Quét Form rõ ràng và đồng bộ panel."""
        compact_message = " ".join(str(message).split())
        self._log(f"⚠ {compact_message}", "warning")
        self._refresh_sched_form_guidance(subject_error=message)
        messagebox.showwarning(title, message)

    def _preflight_khdh_schedule_context(self, port):
        """Kiểm tra live page có đúng context tối thiểu trước khi chạy/resume KHDH."""
        bridge = None
        try:
            bridge = ChromeBridge(port=port)
            ok, msg = bridge.connect()
            if not ok:
                return False, f"Kết nối CDP thất bại: {msg}"
            ok_probe, probe_msg, _probe = bridge.probe_khdh_schedule_context()
            if not ok_probe:
                return False, probe_msg
            return True, probe_msg
        except Exception as e:
            return False, f"Lỗi preflight KHDH: {type(e).__name__}: {str(e)[:120]}"
        finally:
            if bridge:
                try:
                    bridge.disconnect()
                except Exception:
                    pass

    def _build_pending_schedule_items(self, params, resume_state):
        """Dựng danh sách slot còn lại khi schedule bị dừng giữa chừng."""
        pending = []
        if not params or not resume_state:
            return pending

        schedule_mode = str(params.get("schedule_mode", SCHEDULE_MODE_MANUAL) or SCHEDULE_MODE_MANUAL)
        if schedule_mode == SCHEDULE_MODE_KHDH:
            tuan_to = int(params.get("tuan_to", 0) or 0)
            tuan_from = int(params.get("tuan_from", 1) or 1)
            lop_list = list(params.get("lop_list") or [params.get("lop")])
            next_lop_idx = int(resume_state.get("next_lop_idx", 0) or 0)
            next_tuan_num = resume_state.get("next_tuan_num")
            next_slot_idx = int(resume_state.get("next_slot_idx", 0) or 0)
            next_row_key = str(resume_state.get("next_row_key", "") or "").strip()
            if next_tuan_num is None or next_lop_idx >= len(lop_list):
                return pending
            for lop_idx in range(next_lop_idx, len(lop_list)):
                lop_text = str(lop_list[lop_idx] or "").strip()
                if not lop_text:
                    continue
                start_week = int(next_tuan_num) if lop_idx == next_lop_idx else tuan_from
                for tuan_num in range(start_week, tuan_to + 1):
                    if lop_idx == next_lop_idx and tuan_num == int(next_tuan_num):
                        message = (
                            f"Lớp {lop_text}: tiếp tục từ row gợi ý KHDH #{next_slot_idx + 1}"
                            if next_slot_idx > 0 else
                            f"Lớp {lop_text}: chưa xử lý xong row gợi ý KHDH của tuần này"
                        )
                        if next_row_key:
                            message += f" | key={next_row_key}"
                    else:
                        message = f"Lớp {lop_text}: chưa quét row gợi ý KHDH của tuần này"
                    pending.append({
                        "week_text": f"Tuần {tuan_num}",
                        "slot_label": "Các row chữ đỏ theo KHDH",
                        "message": message,
                    })
            return pending

        slots = params.get("slots", [])
        tuan_to = int(params.get("tuan_to", 0) or 0)
        next_tuan_num = resume_state.get("next_tuan_num")
        next_slot_idx = resume_state.get("next_slot_idx", 0)
        if not slots or next_tuan_num is None or next_tuan_num > tuan_to:
            return pending

        for tuan_num in range(int(next_tuan_num), tuan_to + 1):
            start_idx = int(next_slot_idx) if tuan_num == int(next_tuan_num) else 0
            for slot_idx in range(start_idx, len(slots)):
                slot = slots[slot_idx]
                pending.append({
                    "week_text": f"Tuần {tuan_num}",
                    "slot_label": self._format_schedule_slot_label(
                        slot.get("thu", "?"),
                        slot.get("buoi", "?"),
                        slot.get("tiet", "?"),
                    ),
                    "message": "Chưa thực hiện xong trong phiên vừa rồi",
                })
        return pending

    def _show_schedule_summary(self, summary):
        """Hiển thị bảng tổng kết chi tiết sau khi schedule hoàn tất hoặc tạm dừng."""
        if self._schedule_summary_window and self._schedule_summary_window.winfo_exists():
            try:
                self._schedule_summary_window.destroy()
            except Exception:
                pass

        window = tk.Toplevel(self.root)
        window.title("Tổng kết Schedule")
        window.transient(self.root)
        window.geometry("820x520")
        window.minsize(700, 420)
        self._schedule_summary_window = window

        is_stopped = bool(summary.get("stopped"))
        completed = int(summary.get("completed", 0) or 0)
        skipped = int(summary.get("skipped", 0) or 0)
        error_count = int(summary.get("errors", 0) or 0)
        stop_reason = summary.get("stop_reason") or "người dùng"
        pending_items = summary.get("pending_items", []) or []
        next_ppct = summary.get("next_ppct")

        header = ttk.Frame(window, padding=10)
        header.pack(fill="x")
        title_text = "⏸ Schedule tạm dừng" if is_stopped else "✅ Schedule hoàn tất"
        title_fg = "#b00020" if (is_stopped or error_count > 0) else "#1f7a1f"
        tk.Label(
            header,
            text=title_text,
            font=("Segoe UI", 12, "bold"),
            fg=title_fg,
            anchor="w",
        ).pack(fill="x")

        summary_lines = [
            f"Thành công: {completed}",
            f"Đã có dữ liệu: {skipped}",
            f"Lỗi: {error_count}",
        ]
        if is_stopped:
            summary_lines.append(f"Dừng bởi: {stop_reason}")
        if next_ppct is not None:
            summary_lines.append(f"PPCT kế tiếp nội bộ: {next_ppct}")

        tk.Label(
            header,
            text=" | ".join(summary_lines),
            font=("Segoe UI", 9),
            anchor="w",
            justify="left",
        ).pack(fill="x", pady=(4, 0))

        body = ttk.Frame(window, padding=(10, 0, 10, 10))
        body.pack(fill="both", expand=True)

        text = tk.Text(
            body,
            wrap="word",
            font=("Consolas", 10),
            bg="#ffffff",
            fg="#222222",
            insertbackground="#000000",
        )
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        text.tag_configure("section", font=("Segoe UI", 10, "bold"))
        text.tag_configure("success", foreground="#1f7a1f")
        text.tag_configure("skip", foreground="#666666")
        text.tag_configure("error", foreground="#b00020")
        text.tag_configure("pending", foreground="#b00020")

        results = summary.get("results", []) or []
        if results:
            text.insert("end", "Kết quả đã xử lý\n", "section")
            for item in results:
                week_text = item.get("week_text", "Tuần ?")
                slot_label = item.get("slot_label", "Slot ?")
                ppct = item.get("ppct")
                ppct_text = f" | PPCT {ppct}" if ppct not in (None, "") else ""
                message = item.get("message", "")
                status = item.get("status", "")
                if status == "success":
                    prefix = "[OK]"
                    tag = "success"
                elif status == "skipped_existing":
                    prefix = "[SKIP]"
                    tag = "skip"
                else:
                    prefix = "[LỖI]"
                    tag = "error"
                text.insert(
                    "end",
                    f"{prefix} {week_text} | {slot_label}{ppct_text} | {message}\n",
                    tag,
                )
            text.insert("end", "\n")

        if pending_items:
            text.insert("end", "Chưa hoàn thành / còn lại\n", "section")
            for item in pending_items:
                text.insert(
                    "end",
                    f"[CHƯA XONG] {item.get('week_text', 'Tuần ?')} | "
                    f"{item.get('slot_label', 'Slot ?')} | {item.get('message', '')}\n",
                    "pending",
                )

        text.config(state="disabled")

        footer = ttk.Frame(window, padding=(10, 0, 10, 10))
        footer.pack(fill="x")
        ttk.Button(footer, text="Đóng", command=window.destroy).pack(side="right")
        self._play_completion_notification()
        self._bring_window_to_front(window)

    def _play_completion_notification(self):
        """Phát âm báo ngắn khi schedule hiển thị bảng tổng kết."""
        try:
            if winsound is not None:
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            else:
                self.root.bell()
        except Exception:
            try:
                self.root.bell()
            except Exception:
                pass

    def _bring_window_to_front(self, window):
        """Đưa dialog lên trước mặt người dùng mà không giữ app ở trạng thái always-on-top."""
        if not window or not window.winfo_exists():
            return
        try:
            window.deiconify()
        except Exception:
            pass
        try:
            window.lift()
        except Exception:
            pass
        try:
            window.focus_force()
        except Exception:
            pass
        try:
            window.attributes("-topmost", True)
            window.after(350, lambda: window.winfo_exists() and window.attributes("-topmost", False))
        except Exception:
            pass

    def _destroy_class_stats_dialog(self):
        """Đóng dialog thống kê lớp và clear references."""
        if self._class_stats_dialog and self._class_stats_dialog.winfo_exists():
            try:
                self._class_stats_dialog.destroy()
            except Exception:
                pass
        self._class_stats_dialog = None
        self._class_stats_status_label = None
        self._class_stats_result_text = None
        self._class_stats_buttons_frame = None
        self._class_stats_buttons = {}
        self._class_stats_mon_hoc_options = []
        self.cmb_stats_mon_hoc = None

    def _clear_class_stats_queue(self):
        """Xóa các message thống kê lớp còn tồn trong queue trước phiên mới."""
        try:
            while not self._class_stats_queue.empty():
                self._class_stats_queue.get_nowait()
        except Exception:
            pass

    def _set_class_stats_status(self, text, color="#555"):
        """Cập nhật dòng trạng thái trong dialog thống kê lớp."""
        if self._class_stats_status_label and self._class_stats_status_label.winfo_exists():
            self._class_stats_status_label.config(text=text, foreground=color)

    def _ensure_class_stats_dialog(self):
        """Tạo hoặc focus dialog thống kê lớp."""
        if self._class_stats_dialog and self._class_stats_dialog.winfo_exists():
            self._class_stats_dialog.deiconify()
            self._class_stats_dialog.lift()
            self._class_stats_dialog.focus_force()
            return self._class_stats_dialog

        dlg = tk.Toplevel(self.root)
        dlg.title("Thống kê lớp & PPCT")
        dlg.transient(self.root)
        dlg.geometry("1040x700")
        dlg.minsize(860, 560)
        dlg.protocol("WM_DELETE_WINDOW", self._destroy_class_stats_dialog)
        self._class_stats_dialog = dlg

        header = ttk.Frame(dlg, padding=10)
        header.pack(fill="x")
        ttk.Label(
            header,
            text="Thống kê tình trạng sổ đầu bài theo lớp",
            font=("Segoe UI", 12, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            header,
            text=(
                "Chọn khoảng tuần để nạp đúng danh sách lớp. "
                "Nhóm PPCT dùng Môn học đã chọn; nhóm 'GV chưa nhập' và 'Row đỏ KHBD' không phụ thuộc combobox Môn học."
            ),
            foreground="#666",
            wraplength=980,
            justify="left",
        ).pack(anchor="w", pady=(2, 0))

        row_range = ttk.Frame(dlg, padding=(10, 0, 10, 6))
        row_range.pack(fill="x")
        ttk.Label(row_range, text="Tuần từ:").pack(side="left")
        ttk.Spinbox(
            row_range, from_=1, to=52, width=5, textvariable=self.var_stats_tuan_from
        ).pack(side="left", padx=4)
        ttk.Label(row_range, text="→").pack(side="left", padx=2)
        ttk.Spinbox(
            row_range, from_=1, to=52, width=5, textvariable=self.var_stats_tuan_to
        ).pack(side="left", padx=4)
        ttk.Button(
            row_range,
            text="↻ Tải lại DS lớp",
            command=self._load_class_stats_options,
        ).pack(side="right")

        row_subject = ttk.Frame(dlg, padding=(10, 0, 10, 6))
        row_subject.pack(fill="x")
        ttk.Label(row_subject, text="Môn học (chỉ cho PPCT):").pack(side="left")
        self.cmb_stats_mon_hoc = ttk.Combobox(
            row_subject,
            textvariable=self.var_stats_mon_hoc,
            state="readonly",
            width=34,
        )
        self.cmb_stats_mon_hoc.pack(side="left", padx=4, fill="x", expand=True)

        row_actions = ttk.Frame(dlg, padding=(10, 0, 10, 8))
        row_actions.pack(fill="x")
        ttk.Label(row_actions, text="PPCT:").pack(side="left")
        self.btn_stats_all_classes = ttk.Button(
            row_actions,
            text="⚡ PPCT cao nhất các lớp",
            command=self._on_run_all_class_stats,
        )
        self.btn_stats_all_classes.pack(side="left", padx=(6, 0))
        self.btn_stats_all_missing = ttk.Button(
            row_actions,
            text="🔎 Thiếu PPCT toàn lớp",
            command=self._on_run_all_class_missing,
        )
        self.btn_stats_all_missing.pack(side="left", padx=(6, 0))
        ttk.Separator(row_actions, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Label(row_actions, text="Theo màn thống kê:").pack(side="left")
        self.btn_stats_missing_teachers = ttk.Button(
            row_actions,
            text="👤 GV chưa nhập theo tuần/lớp",
            command=self._on_run_missing_teacher_audit,
        )
        self.btn_stats_missing_teachers.pack(side="left", padx=(6, 0))
        ttk.Separator(row_actions, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Label(row_actions, text="Theo KHBD:").pack(side="left")
        self.btn_stats_khdh_pending = ttk.Button(
            row_actions,
            text="📝 Row đỏ KHBD chưa lên",
            command=self._on_run_khdh_pending_audit,
        )
        self.btn_stats_khdh_pending.pack(side="left", padx=(6, 0))

        self._class_stats_status_label = ttk.Label(
            dlg,
            text="Chưa quét danh sách lớp và môn học",
            foreground="#555",
            padding=(10, 0, 10, 6),
        )
        self._class_stats_status_label.pack(fill="x")

        classes_frame = ttk.LabelFrame(dlg, text="Lớp hiện có trên sổ đầu bài", padding=6)
        classes_frame.pack(fill="x", padx=10, pady=(0, 8))
        buttons_canvas = tk.Canvas(classes_frame, height=100, highlightthickness=0)
        buttons_scroll = ttk.Scrollbar(
            classes_frame, orient="vertical", command=buttons_canvas.yview
        )
        buttons_canvas.configure(yscrollcommand=buttons_scroll.set)
        buttons_scroll.pack(side="right", fill="y")
        buttons_canvas.pack(side="left", fill="both", expand=True, padx=(0, 2))

        self._class_stats_buttons_frame = ttk.Frame(buttons_canvas)
        self._class_stats_buttons_window = buttons_canvas.create_window(
            (0, 0), window=self._class_stats_buttons_frame, anchor="nw"
        )
        self._class_stats_buttons_frame.bind(
            "<Configure>",
            lambda e: buttons_canvas.configure(scrollregion=buttons_canvas.bbox("all"))
        )
        buttons_canvas.bind(
            "<Configure>",
            lambda e: buttons_canvas.itemconfig(self._class_stats_buttons_window, width=e.width)
        )

        ttk.Label(
            self._class_stats_buttons_frame,
            text="Bấm 'Quét lớp & thống kê PPCT' hoặc 'Tải lại DS lớp' để nạp danh sách.",
            foreground="gray",
        ).grid(row=0, column=0, sticky="w", padx=4, pady=4)

        result_frame = ttk.LabelFrame(dlg, text="Kết quả thống kê", padding=6)
        result_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self._class_stats_result_text = tk.Text(
            result_frame,
            wrap="word",
            font=("Consolas", 10),
            bg="#ffffff",
            fg="#222222",
            insertbackground="#000000",
            padx=6,
            pady=6,
        )
        result_scroll = ttk.Scrollbar(
            result_frame, orient="vertical", command=self._class_stats_result_text.yview
        )
        self._class_stats_result_text.configure(yscrollcommand=result_scroll.set)
        self._class_stats_result_text.pack(side="left", fill="both", expand=True)
        result_scroll.pack(side="right", fill="y")
        self._class_stats_result_text.tag_configure("title", font=("Segoe UI", 10, "bold"))
        self._class_stats_result_text.tag_configure("ok", foreground="#1f7a1f")
        self._class_stats_result_text.tag_configure("warn", foreground="#a86400")
        self._class_stats_result_text.tag_configure("error", foreground="#b00020")
        self._class_stats_result_text.tag_configure("muted", foreground="#666666")
        self._class_stats_result_text.tag_configure("section", font=("Segoe UI", 10, "bold"), foreground="#1f2937")
        self._class_stats_result_text.insert(
            "end",
            "Kết quả sẽ hiện ở đây sau khi bạn chọn một lớp.\n",
            "muted",
        )
        self._class_stats_result_text.config(state="disabled")
        return dlg

    def _populate_class_stats_buttons(self, lop_options):
        """Hiển thị nút lớp trong dialog thống kê."""
        self._class_stats_lop_options = list(lop_options)
        if not self._class_stats_buttons_frame or not self._class_stats_buttons_frame.winfo_exists():
            return

        for widget in self._class_stats_buttons_frame.winfo_children():
            widget.destroy()
        self._class_stats_buttons = {}

        if not lop_options:
            ttk.Label(
                self._class_stats_buttons_frame,
                text="Không có lớp nào khả dụng.",
                foreground="gray",
            ).grid(row=0, column=0, sticky="w", padx=4, pady=4)
            return

        cols = 6
        for idx, lop_text in enumerate(lop_options):
            btn = ttk.Button(
                self._class_stats_buttons_frame,
                text=lop_text,
                command=lambda value=lop_text: self._on_run_class_stats(value),
            )
            btn.grid(row=idx // cols, column=idx % cols, sticky="ew", padx=3, pady=3)
            self._class_stats_buttons[lop_text] = btn

        for col_idx in range(cols):
            self._class_stats_buttons_frame.grid_columnconfigure(col_idx, weight=1)

    def _populate_class_stats_mon_hoc(self, mon_hoc_options):
        """Populate combobox Môn học cho module thống kê lớp."""
        self._class_stats_mon_hoc_options = list(mon_hoc_options or [])
        if not self.cmb_stats_mon_hoc or not self.cmb_stats_mon_hoc.winfo_exists():
            return ""

        preferred_text = self.var_stats_mon_hoc.get() or self.var_sched_mon_hoc.get()
        return self._set_sched_combobox_selection(
            self.cmb_stats_mon_hoc,
            self.var_stats_mon_hoc,
            self._class_stats_mon_hoc_options,
            preferred_text=preferred_text,
        )

    @staticmethod
    def _normalize_class_stats_subject(text):
        """Chuẩn hóa text môn học để so khớp ổn định giữa bảng và dropdown."""
        value = str(text or "").replace("\n", " ")
        value = unicodedata.normalize("NFD", value)
        value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
        value = re.sub(r"\s+", " ", value).strip().casefold()
        return value

    @classmethod
    def _get_class_stats_subject_candidates(cls, text):
        """Sinh các dạng text môn học có thể match được trên bảng."""
        raw = re.sub(r"\s+", " ", str(text or "").replace("\n", " ")).strip()
        variants = [raw]
        base_text = re.sub(r"\s*\([^)]*\)\s*", " ", raw).strip()
        if base_text:
            variants.append(base_text)

        normalized = []
        for item in variants:
            key = cls._normalize_class_stats_subject(item)
            if key and key not in normalized:
                normalized.append(key)
        return normalized

    def _class_stats_row_matches_subject(self, row_mon_hoc, target_mon_hoc):
        """Kiểm tra row bảng có thuộc môn học người dùng đang thống kê hay không."""
        target_key = self._normalize_class_stats_subject(target_mon_hoc)
        if not target_key:
            return False
        return target_key in self._get_class_stats_subject_candidates(row_mon_hoc)

    def _class_stats_cache_key(self, lop_text, tuan_num):
        """Sinh cache key ổn định theo lớp và tuần."""
        return (str(lop_text).strip().lower(), int(tuan_num))

    @staticmethod
    def _is_class_stats_cache_fresh(saved_at, now_ts=None):
        """Kiểm tra entry cache còn trong TTL hay không."""
        try:
            saved_ts = float(saved_at or 0)
        except Exception:
            return False
        if saved_ts <= 0:
            return False
        current_ts = float(now_ts if now_ts is not None else time.time())
        return (current_ts - saved_ts) <= float(CLASS_STATS_CACHE_TTL_SECONDS)

    def _class_stats_cache_file_path(self):
        """Đường dẫn file sidecar cache PPCT."""
        return os.path.join(os.path.dirname(__file__), CLASS_STATS_CACHE_FILE)

    def _read_class_stats_cache_entry_locked(self, cache_key, now_ts=None):
        """Đọc cache payload theo key (yêu cầu đã giữ lock)."""
        payload = self._class_stats_fetch_cache.get(cache_key)
        if payload is None:
            return None
        saved_at = self._class_stats_fetch_cache_saved_at.get(cache_key)
        if not self._is_class_stats_cache_fresh(saved_at, now_ts=now_ts):
            self._class_stats_fetch_cache.pop(cache_key, None)
            self._class_stats_fetch_cache_saved_at.pop(cache_key, None)
            return None
        return copy.deepcopy(payload)

    def _write_class_stats_cache_entry_locked(self, cache_key, payload, saved_at=None):
        """Ghi cache payload theo key (yêu cầu đã giữ lock)."""
        payload_copy = copy.deepcopy(payload)
        self._class_stats_fetch_cache[cache_key] = payload_copy
        self._class_stats_fetch_cache_saved_at[cache_key] = float(
            saved_at if saved_at is not None else time.time()
        )
        return payload_copy

    def _save_class_stats_cache_to_disk(self):
        """Lưu cache PPCT xuống sidecar JSON để tái sử dụng ở lần mở app kế tiếp."""
        try:
            now_ts = time.time()
            entries = []
            with self._class_stats_fetch_cache_lock:
                for cache_key, payload in self._class_stats_fetch_cache.items():
                    saved_at = self._class_stats_fetch_cache_saved_at.get(cache_key)
                    if not self._is_class_stats_cache_fresh(saved_at, now_ts=now_ts):
                        continue
                    lop_key, tuan_num = cache_key
                    entries.append({
                        "lop": str(lop_key),
                        "tuan": int(tuan_num),
                        "saved_at": float(saved_at),
                        "payload": copy.deepcopy(payload),
                    })
            entries.sort(key=lambda item: float(item.get("saved_at", 0.0)), reverse=True)
            entries = entries[:CLASS_STATS_CACHE_MAX_ENTRIES]
            payload = {
                "version": 1,
                "ttl_seconds": int(CLASS_STATS_CACHE_TTL_SECONDS),
                "saved_at": now_ts,
                "entries": entries,
            }
            cache_path = self._class_stats_cache_file_path()
            self._write_json_atomic(cache_path, payload)
        except Exception as e:
            self._log(f"Lỗi save cache PPCT: {e}", "warning")

    def _load_class_stats_cache_from_disk(self):
        """Nạp cache PPCT từ sidecar JSON, tự bỏ entry hết hạn."""
        cache_path = self._class_stats_cache_file_path()
        if not os.path.exists(cache_path):
            return
        try:
            with open(cache_path, "r", encoding="utf-8") as cache_file:
                raw = json.load(cache_file)
        except Exception as e:
            self._log(f"Lỗi load cache PPCT: {e}", "warning")
            return

        entries = list((raw or {}).get("entries") or [])
        now_ts = time.time()
        loaded = 0
        with self._class_stats_fetch_cache_lock:
            self._class_stats_fetch_cache.clear()
            self._class_stats_fetch_cache_saved_at.clear()
            for item in entries:
                try:
                    lop_key = str(item.get("lop", "")).strip().lower()
                    tuan_num = int(item.get("tuan", 0))
                    saved_at = float(item.get("saved_at", 0))
                    payload = item.get("payload")
                except Exception:
                    continue
                if not lop_key or tuan_num < 1 or payload is None:
                    continue
                if not self._is_class_stats_cache_fresh(saved_at, now_ts=now_ts):
                    continue
                cache_key = (lop_key, tuan_num)
                self._write_class_stats_cache_entry_locked(cache_key, payload, saved_at=saved_at)
                loaded += 1
        if loaded:
            self._log(f"Đã nạp cache PPCT: {loaded} tuần (TTL {int(CLASS_STATS_CACHE_TTL_SECONDS/3600)}h).", "info")

    def _invalidate_class_stats_cache(self):
        """Xóa cache thống kê lớp trong session hiện tại."""
        with self._class_stats_fetch_cache_lock:
            self._class_stats_fetch_cache.clear()
            self._class_stats_fetch_cache_saved_at.clear()

    def _fetch_class_stats_week_payload(self, bridge, lop_text, tuan_num, class_meta=None):
        """Lấy payload sổ đầu bài theo lớp/tuần với cache và retry ngắn."""
        cache_key = self._class_stats_cache_key(lop_text, tuan_num)
        now_ts = time.time()
        with self._class_stats_fetch_cache_lock:
            cached_payload = self._read_class_stats_cache_entry_locked(cache_key, now_ts=now_ts)
        if cached_payload is not None:
            return True, copy.deepcopy(cached_payload), {
                "from_cache": True,
                "attempts": 0,
                "timeout_s": 0.0,
            }

        attempts = (5.5, 9.0)
        last_error = "Không lấy được dữ liệu tuần"
        for idx, timeout_s in enumerate(attempts, start=1):
            ok, payload = bridge.fetch_sodaubai_rows(
                lop_text,
                tuan_num,
                timeout_s=timeout_s,
                class_meta=class_meta,
            )
            if ok:
                fetched_week = payload.get("week")
                if fetched_week is not None and int(fetched_week) != int(tuan_num):
                    last_error = (
                        f"Service trả về tuần {fetched_week}, "
                        f"không khớp tuần yêu cầu {tuan_num}"
                    )
                    continue
                payload_copy = copy.deepcopy(payload)
                with self._class_stats_fetch_cache_lock:
                    self._write_class_stats_cache_entry_locked(cache_key, payload_copy)
                return True, copy.deepcopy(payload_copy), {
                    "from_cache": False,
                    "attempts": idx,
                    "timeout_s": timeout_s,
                }
            last_error = str(payload)

        return False, last_error, {
            "from_cache": False,
            "attempts": len(attempts),
            "timeout_s": attempts[-1],
        }

    @staticmethod
    def _resolve_class_stats_bulk_concurrency(requested_concurrency, missing_week_count):
        """Chọn concurrency an toàn cho bulk fetch để giảm abort khi quét dải tuần dài."""
        requested = max(1, int(requested_concurrency or 1))
        missing_count = max(0, int(missing_week_count or 0))
        if missing_count <= 0:
            return 1
        if missing_count >= 30:
            ceiling = 2
        elif missing_count >= 16:
            ceiling = 3
        elif missing_count >= 8:
            ceiling = 4
        else:
            ceiling = 6
        return max(1, min(requested, ceiling, missing_count))

    def _fetch_class_stats_week_payloads_bulk(self, bridge, lop_text, week_numbers, concurrency=6,
                                              class_meta=None):
        """Lấy nhiều payload lớp/tuần với cache trước, bulk fetch sau, fallback tuần tự nếu cần."""
        ordered_weeks = []
        seen = set()
        for item in list(week_numbers or []):
            try:
                week_num = int(item)
            except Exception:
                continue
            if week_num < 1 or week_num in seen:
                continue
            seen.add(week_num)
            ordered_weeks.append(week_num)

        payload_map = {}
        week_errors = []
        cache_hits = 0
        bulk_hits = 0
        fallback_hits = 0
        missing_weeks = []
        requested_concurrency = max(1, int(concurrency or 1))
        effective_concurrency = 0

        now_ts = time.time()
        with self._class_stats_fetch_cache_lock:
            for week_num in ordered_weeks:
                cache_key = self._class_stats_cache_key(lop_text, week_num)
                cached_payload = self._read_class_stats_cache_entry_locked(cache_key, now_ts=now_ts)
                if cached_payload is not None:
                    payload_map[week_num] = copy.deepcopy(cached_payload)
                    cache_hits += 1
                else:
                    missing_weeks.append(week_num)

        failed_weeks = {}
        if missing_weeks:
            effective_concurrency = self._resolve_class_stats_bulk_concurrency(
                requested_concurrency,
                len(missing_weeks),
            )
            bulk_timeout = max(12.0, min(28.0, 5.0 + len(missing_weeks) * 0.55))
            ok_bulk, bulk_payload_or_error = bridge.fetch_sodaubai_rows_bulk(
                lop_text,
                missing_weeks,
                timeout_s=bulk_timeout,
                concurrency=effective_concurrency,
                class_meta=class_meta,
            )
            if ok_bulk:
                for item in list((bulk_payload_or_error or {}).get("results") or []):
                    try:
                        requested_week = int(item.get("requested_week"))
                    except Exception:
                        continue
                    if item.get("ok"):
                        payload = copy.deepcopy(item.get("payload") or {})
                        payload_map[requested_week] = payload
                        with self._class_stats_fetch_cache_lock:
                            self._write_class_stats_cache_entry_locked(
                                self._class_stats_cache_key(lop_text, requested_week),
                                payload,
                            )
                        bulk_hits += 1
                    else:
                        failed_weeks[requested_week] = str(item.get("error", "Bulk fetch thất bại"))
            else:
                for week_num in missing_weeks:
                    failed_weeks[week_num] = str(bulk_payload_or_error)

        for week_num in missing_weeks:
            if week_num in payload_map:
                continue
            ok, payload_or_error, _meta = self._fetch_class_stats_week_payload(
                bridge,
                lop_text,
                week_num,
                class_meta=class_meta,
            )
            if ok:
                payload_map[week_num] = payload_or_error
                fallback_hits += 1
            else:
                week_errors.append({
                    "week": week_num,
                    "message": failed_weeks.get(week_num, str(payload_or_error)),
                })

        return payload_map, week_errors, {
            "cache_hits": cache_hits,
            "bulk_hits": bulk_hits,
            "fallback_hits": fallback_hits,
            "requested_concurrency": requested_concurrency,
            "effective_concurrency": effective_concurrency,
        }

    def _extract_class_stats_subject_rows(self, table_rows, tuan_num, target_mon_hoc):
        """Lọc các row thuộc môn đã chọn và trích xuất occurrence/PPCT."""
        tuan_text = f"Tuần {tuan_num}"
        occurrences = []
        invalid_ppct_rows = []
        total_rows_with_data = 0

        for row in list(table_rows or []):
            if not row.get("has_data"):
                continue

            slot_label = self._format_schedule_slot_label(
                row.get("thu", "?"),
                row.get("buoi", "?"),
                row.get("tiet", "?"),
            )
            ppct_text = str(row.get("ppct", "")).strip()
            mon_hoc = str(row.get("mon_hoc", "")).strip() or "(Không rõ môn)"
            if not self._class_stats_row_matches_subject(mon_hoc, target_mon_hoc):
                continue

            total_rows_with_data += 1
            occurrence = {
                "week": tuan_num,
                "week_text": tuan_text,
                "slot_label": slot_label,
                "ppct_text": ppct_text,
                "mon_hoc": mon_hoc,
            }

            match = re.search(r"\d+", ppct_text)
            if not match:
                invalid_ppct_rows.append({
                    **occurrence,
                    "message": "Có dữ liệu môn học nhưng PPCT trống hoặc không hợp lệ",
                })
                continue

            occurrence["ppct"] = int(match.group())
            occurrences.append(occurrence)

        return occurrences, invalid_ppct_rows, total_rows_with_data

    def _load_class_stats_options(self):
        """Tải danh sách lớp và tuần thực tế từ VnEdu để phục vụ thống kê."""
        if not self._cdp_connected:
            self._log("Chưa kết nối CDP!", "error")
            messagebox.showwarning("Cảnh báo", "Hãy kết nối CDP trước khi quét lớp.")
            return
        if self._class_stats_thread and self._class_stats_thread.is_alive():
            self._log("Đang có một tác vụ thống kê lớp chạy rồi.", "warning")
            return
        if self._schedule_running:
            messagebox.showwarning(
                "Cảnh báo",
                "Schedule đang chạy. Hãy dừng hoặc chờ xong rồi mới quét thống kê lớp.",
            )
            return
        self._ensure_class_stats_dialog()
        self._clear_class_stats_queue()
        tuan_nums = self._week_numbers_from_vars(
            self.var_stats_tuan_from,
            self.var_stats_tuan_to,
            require_multi_week=True,
        )
        scan_scope = (
            f"Tuần {tuan_nums[0]}→{tuan_nums[-1]}" if tuan_nums else "toàn bộ tuần có trên web"
        )
        self._set_class_stats_status(
            f"⏳ Đang tải danh sách lớp và tuần ({scan_scope})...",
            "#a86400",
        )
        cached_mon_hoc_options = copy.deepcopy(self._sched_mon_hoc_options)

        def _work():
            try:
                bridge = ChromeBridge(port=self._cdp_port)
                ok, msg = bridge.connect()
                if not ok:
                    self._class_stats_queue.put(("options_error", f"Kết nối CDP thất bại: {msg}"))
                    return
                ok_lop, lop_data = bridge.discover_lop_options_for_weeks(tuan_nums=tuan_nums)
                ok_tuan, tuan_data = bridge.get_tuan_options()
                ok_mon, mon_data = bridge.read_form_options(row_index=0)
                bridge.disconnect()
                if not ok_lop:
                    self._class_stats_queue.put(("options_error", f"Lỗi tải lớp: {lop_data}"))
                    return
                if not ok_tuan:
                    self._class_stats_queue.put(("options_error", f"Lỗi tải tuần: {tuan_data}"))
                    return
                mon_hoc_options = []
                mon_hoc_note = ""
                if ok_mon:
                    mon_hoc_options = list((mon_data or {}).get("mon_hoc", []) or [])
                elif cached_mon_hoc_options:
                    mon_hoc_options = list(cached_mon_hoc_options)
                    mon_hoc_note = f"Không quét được danh sách môn từ form, dùng cache cũ: {mon_data}"
                else:
                    self._class_stats_queue.put(
                        ("options_error", f"Lỗi tải môn học từ form: {mon_data}")
                    )
                    return
                self._class_stats_queue.put(("options_ready", {
                    "lop_options": self._sort_lop_options(lop_data.get("options", [])),
                    "lop_records": list(lop_data.get("records", []) or []),
                    "tuan_options": tuan_data,
                    "mon_hoc_options": mon_hoc_options,
                    "mon_hoc_note": mon_hoc_note,
                    "class_scan_scope": scan_scope,
                    "class_weeks_scanned": len(lop_data.get("weeks_scanned", []) or []),
                }))
            except Exception as e:
                self._class_stats_queue.put(("options_error", f"Exception tải lớp: {e}"))

        self._class_stats_thread = threading.Thread(target=_work, daemon=True)
        self._class_stats_thread.start()
        self.root.after(100, self._poll_class_stats_queue)

    def _on_open_class_stats_dialog(self):
        """Mở dialog thống kê lớp và quét danh sách lớp hiện có."""
        self._ensure_class_stats_dialog()
        self._load_class_stats_options()

    # =================================================================
    # XÓA DỮ LIỆU SỔ ĐẦU BÀI (theo khoảng tuần) — API-first an toàn
    # =================================================================

    def _destroy_delete_dialog(self):
        """Đóng dialog xóa và clear references."""
        if self._delete_dialog and self._delete_dialog.winfo_exists():
            try:
                self._delete_dialog.destroy()
            except Exception:
                pass
        self._delete_dialog = None
        self._delete_status_label = None
        self._delete_result_text = None
        self._delete_run_button = None
        self._delete_stop_button = None
        self._delete_scan_button = None
        self._delete_class_combo = None
        self._delete_scanned_entries = []
        self._delete_scan_signature = None
        self._delete_scanning = False

    def _clear_delete_queue(self):
        """Xóa message tồn trong queue trước phiên xóa mới."""
        try:
            while not self._delete_queue.empty():
                self._delete_queue.get_nowait()
        except Exception:
            pass

    def _set_delete_status(self, text, color="#555"):
        """Cập nhật dòng trạng thái trong dialog xóa."""
        if self._delete_status_label and self._delete_status_label.winfo_exists():
            self._delete_status_label.config(text=text, foreground=color)

    def _delete_log(self, message, tag="muted"):
        """Ghi 1 dòng vào vùng kết quả của dialog xóa."""
        widget = self._delete_result_text
        if widget is None or not widget.winfo_exists():
            return
        widget.config(state="normal")
        widget.insert("end", message + "\n", tag)
        widget.see("end")
        widget.config(state="disabled")

    def _delete_busy_reason(self):
        """Trả về lý do KHÔNG được phép xóa/quét lúc này (concurrency guard)."""
        if self._schedule_running:
            return "Schedule đang chạy. Hãy dừng hoặc chờ xong rồi mới thao tác xóa."
        if self._class_stats_running or (
            self._class_stats_thread and self._class_stats_thread.is_alive()
        ):
            return "Đang có phiên thống kê lớp dùng CDP. Hãy chờ xong rồi mới thao tác xóa."
        if self._auto_login_running:
            return "Auto-login đang chạy. Hãy chờ xong rồi mới thao tác xóa."
        if self._quick_prepare_running:
            return "Đang quét full dữ liệu. Hãy chờ xong rồi mới thao tác xóa."
        return ""

    def _compute_delete_scan_signature(self, lop_text, tuan_from, tuan_to, only_mine):
        """Chữ ký ngữ cảnh scan để phát hiện thay đổi trước khi xóa."""
        return (
            str(lop_text or "").strip().lower(),
            int(tuan_from),
            int(tuan_to),
            bool(only_mine),
        )

    def _current_delete_signature(self):
        """Chữ ký theo các lựa chọn hiện tại trên dialog xóa."""
        try:
            tuan_from = int(self.var_delete_tuan_from.get())
            tuan_to = int(self.var_delete_tuan_to.get())
        except (tk.TclError, ValueError):
            return None
        if tuan_from > tuan_to:
            tuan_from, tuan_to = tuan_to, tuan_from
        return self._compute_delete_scan_signature(
            self.var_delete_lop.get().strip(),
            tuan_from,
            tuan_to,
            bool(self.var_delete_only_mine.get()),
        )

    def _invalidate_delete_scan(self, reason=""):
        """Hủy kết quả scan hiện có (buộc quét lại trước khi xóa)."""
        self._delete_scanned_entries = []
        self._delete_scan_signature = None
        if self._delete_run_button and self._delete_run_button.winfo_exists():
            self._delete_run_button.config(state="disabled")
        if reason and self._delete_status_label and self._delete_status_label.winfo_exists():
            self._set_delete_status(reason, "#a86400")

    def _on_open_delete_dialog(self):
        """Mở dialog Xóa dữ liệu sổ đầu bài."""
        if not self._cdp_connected:
            self._log("Chưa kết nối CDP!", "warning")
            messagebox.showwarning("Cảnh báo", "Hãy kết nối CDP trước khi xóa dữ liệu.")
            return
        busy = self._delete_busy_reason()
        if busy:
            messagebox.showwarning("Cảnh báo", busy)
            return
        self._ensure_delete_dialog()
        self._load_delete_lop_options()

    def _ensure_delete_dialog(self):
        """Tạo hoặc focus dialog xóa dữ liệu."""
        if self._delete_dialog and self._delete_dialog.winfo_exists():
            self._delete_dialog.deiconify()
            self._delete_dialog.lift()
            self._delete_dialog.focus_force()
            return self._delete_dialog

        dlg = tk.Toplevel(self.root)
        dlg.title("Xóa dữ liệu sổ đầu bài")
        dlg.transient(self.root)
        dlg.geometry("900x560")
        dlg.minsize(720, 440)
        dlg.protocol("WM_DELETE_WINDOW", self._on_delete_dialog_close)
        self._delete_dialog = dlg

        header = ttk.Frame(dlg, padding=10)
        header.pack(fill="x")
        tk.Label(
            header,
            text="⚠ XÓA DỮ LIỆU SỔ ĐẦU BÀI — KHÔNG THỂ HOÀN TÁC",
            font=("Segoe UI", 11, "bold"),
            fg="#b00020",
        ).pack(anchor="w")
        ttk.Label(
            header,
            text=(
                "Chọn Lớp và khoảng Tuần, bấm 'Quét tiết đã ghi' để xem preview, "
                "rồi gõ xác nhận để xóa. Mặc định chỉ xóa tiết do CHÍNH BẠN ký tên."
            ),
            foreground="#666",
            wraplength=850,
            justify="left",
        ).pack(anchor="w", pady=(2, 0))

        row_top = ttk.Frame(dlg, padding=(10, 0, 10, 6))
        row_top.pack(fill="x")
        ttk.Label(row_top, text="Lớp:").pack(side="left")
        self._delete_class_combo = ttk.Combobox(
            row_top, textvariable=self.var_delete_lop, state="readonly", width=12
        )
        self._delete_class_combo.pack(side="left", padx=4)
        self._delete_class_combo.bind(
            "<<ComboboxSelected>>",
            lambda _e: self._invalidate_delete_scan(
                "Đã đổi Lớp — hãy bấm Quét lại trước khi xóa."
            ),
        )
        ttk.Label(row_top, text="  Tuần từ:").pack(side="left")
        ttk.Spinbox(
            row_top, from_=1, to=52, width=5, textvariable=self.var_delete_tuan_from,
            command=lambda: self._invalidate_delete_scan(
                "Đã đổi khoảng Tuần — hãy bấm Quét lại trước khi xóa."
            ),
        ).pack(side="left", padx=4)
        ttk.Label(row_top, text="→").pack(side="left", padx=2)
        ttk.Spinbox(
            row_top, from_=1, to=52, width=5, textvariable=self.var_delete_tuan_to,
            command=lambda: self._invalidate_delete_scan(
                "Đã đổi khoảng Tuần — hãy bấm Quét lại trước khi xóa."
            ),
        ).pack(side="left", padx=4)
        ttk.Button(
            row_top, text="↻ Tải lại DS lớp", command=self._load_delete_lop_options
        ).pack(side="right")

        row_opt = ttk.Frame(dlg, padding=(10, 0, 10, 6))
        row_opt.pack(fill="x")
        ttk.Checkbutton(
            row_opt,
            text="Chỉ xóa tiết do CHÍNH TÔI ký tên (an toàn — không đụng dữ liệu giáo viên khác)",
            variable=self.var_delete_only_mine,
            command=lambda: self._invalidate_delete_scan(
                "Đã đổi bộ lọc giáo viên — hãy bấm Quét lại trước khi xóa."
            ),
        ).pack(side="left")

        row_scan = ttk.Frame(dlg, padding=(10, 0, 10, 6))
        row_scan.pack(fill="x")
        self._delete_scan_button = ttk.Button(
            row_scan, text="🔍 Quét tiết đã ghi", command=self._on_delete_scan
        )
        self._delete_scan_button.pack(side="left")

        self._delete_status_label = ttk.Label(
            dlg, text="Chưa quét.", foreground="#555", padding=(10, 0, 10, 6)
        )
        self._delete_status_label.pack(fill="x")

        # Pack các hàng thao tác ở ĐÁY trước (side="bottom") để chúng luôn được
        # giữ chỗ, không bao giờ bị vùng preview (expand) đẩy khuất khỏi dialog.
        row_btn = ttk.Frame(dlg, padding=(10, 4, 10, 10))
        row_btn.pack(side="bottom", fill="x")
        self._delete_run_button = ttk.Button(
            row_btn, text="🗑 XÓA CÁC TIẾT ĐÃ QUÉT",
            command=self._on_delete_run, state="disabled"
        )
        self._delete_run_button.pack(side="left")
        self._delete_stop_button = ttk.Button(
            row_btn, text="⏹ Dừng", command=self._on_delete_stop, state="disabled"
        )
        self._delete_stop_button.pack(side="left", padx=6)
        ttk.Button(row_btn, text="Đóng", command=self._on_delete_dialog_close).pack(side="right")

        row_confirm = ttk.Frame(dlg, padding=(10, 0, 10, 0))
        row_confirm.pack(side="bottom", fill="x")
        ttk.Label(
            row_confirm,
            text="Gõ XOA để xác nhận:",
            foreground="#b00020",
        ).pack(side="left")
        ttk.Entry(
            row_confirm, textvariable=self.var_delete_confirm, width=12
        ).pack(side="left", padx=6)

        # Vùng preview chiếm phần giữa còn lại; Text giới hạn chiều cao để không
        # đẩy các nút phía dưới ra ngoài dialog.
        result_frame = ttk.LabelFrame(dlg, text="Preview các tiết sẽ xóa", padding=6)
        result_frame.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        self._delete_result_text = tk.Text(
            result_frame, height=8, wrap="word", font=("Consolas", 10),
            bg="#ffffff", fg="#222222", insertbackground="#000000",
        )
        result_scroll = ttk.Scrollbar(
            result_frame, orient="vertical", command=self._delete_result_text.yview
        )
        self._delete_result_text.configure(yscrollcommand=result_scroll.set)
        self._delete_result_text.pack(side="left", fill="both", expand=True)
        result_scroll.pack(side="right", fill="y")
        self._delete_result_text.tag_configure("title", font=("Segoe UI", 10, "bold"))
        self._delete_result_text.tag_configure("ok", foreground="#1f7a1f")
        self._delete_result_text.tag_configure("warn", foreground="#a86400")
        self._delete_result_text.tag_configure("error", foreground="#b00020")
        self._delete_result_text.tag_configure("muted", foreground="#666666")
        self._delete_result_text.insert(
            "end", "Bấm 'Quét tiết đã ghi' để xem danh sách tiết có dữ liệu.\n", "muted"
        )
        self._delete_result_text.config(state="disabled")
        return dlg

    def _on_delete_dialog_close(self):
        """Đóng dialog xóa (chặn khi đang chạy)."""
        if self._delete_running:
            messagebox.showwarning(
                "Đang xóa",
                "Tiến trình xóa đang chạy. Hãy bấm Dừng và chờ kết thúc trước khi đóng.",
            )
            return
        self._destroy_delete_dialog()

    def _set_delete_running(self, running):
        """Bật/tắt trạng thái chạy cho cụm xóa."""
        self._delete_running = running
        if self._delete_run_button and self._delete_run_button.winfo_exists():
            self._delete_run_button.config(state="disabled" if running else "normal")
        if self._delete_stop_button and self._delete_stop_button.winfo_exists():
            self._delete_stop_button.config(state="normal" if running else "disabled")
        if self._delete_scan_button and self._delete_scan_button.winfo_exists():
            self._delete_scan_button.config(state="disabled" if running else "normal")

    def _load_delete_lop_options(self):
        """Tải danh sách lớp cho dialog xóa (tái dùng combobox lớp của schedule nếu có)."""
        existing = list(self._delete_class_combo["values"]) if (
            self._delete_class_combo and self._delete_class_combo.winfo_exists()
        ) else []
        sched_values = list(self.cmb_sched_lop["values"]) if hasattr(self, "cmb_sched_lop") else []
        if sched_values:
            self._apply_delete_lop_options(sched_values)
        elif existing:
            pass
        else:
            self._set_delete_status("⏳ Đang tải danh sách lớp...", "#a86400")

        tuan_nums = self._week_numbers_from_vars(
            self.var_delete_tuan_from,
            self.var_delete_tuan_to,
            require_multi_week=True,
        )
        scan_scope = (
            f"Tuần {tuan_nums[0]}→{tuan_nums[-1]}" if tuan_nums else "toàn bộ tuần có trên web"
        )

        def _work():
            try:
                bridge = ChromeBridge(port=self._cdp_port)
                ok, msg = bridge.connect()
                if not ok:
                    bridge.disconnect()
                    self._post_ui(lambda: self._set_delete_status(f"Kết nối CDP lỗi: {msg}", "#b00020"))
                    return
                ok2, lop_data = bridge.discover_lop_options_for_weeks(tuan_nums=tuan_nums)
                bridge.disconnect()
                if ok2:
                    options = self._sort_lop_options(lop_data.get("options", []))
                    self._post_ui(lambda: self._apply_delete_lop_options(options, scan_scope=scan_scope))
                else:
                    self._post_ui(lambda: self._set_delete_status(f"Lỗi tải DS lớp: {lop_data}", "#b00020"))
            except Exception as e:
                self._post_ui(lambda e=e: self._set_delete_status(f"Exception tải DS lớp: {e}", "#b00020"))

        threading.Thread(target=_work, daemon=True).start()

    def _apply_delete_lop_options(self, options, scan_scope=""):
        """Populate combobox lớp trong dialog xóa."""
        if not self._delete_class_combo or not self._delete_class_combo.winfo_exists():
            return
        prev = self.var_delete_lop.get().strip()
        self._delete_class_combo["values"] = options
        if prev and prev in options:
            self._delete_class_combo.set(prev)
        elif self.var_sched_lop.get().strip() in options:
            self._delete_class_combo.set(self.var_sched_lop.get().strip())
        elif options:
            self._delete_class_combo.set(options[0])
        suffix = f" ({scan_scope})" if scan_scope else ""
        self._set_delete_status(
            f"Đã tải {len(options)} lớp{suffix}. Chọn lớp + tuần rồi bấm Quét.",
            "#1f7a1f",
        )

    def _on_delete_scan(self):
        """Quét các tiết đã có dữ liệu trong khoảng tuần để preview trước khi xóa."""
        if self._delete_running or self._delete_scanning:
            return
        if not self._cdp_connected:
            self._set_delete_status("Chưa kết nối CDP!", "#b00020")
            return
        busy = self._delete_busy_reason()
        if busy:
            self._set_delete_status(busy, "#b00020")
            return
        lop_text = self.var_delete_lop.get().strip()
        if not lop_text:
            messagebox.showwarning("Cảnh báo", "Chưa chọn Lớp cần xóa.")
            return
        try:
            tuan_from = int(self.var_delete_tuan_from.get())
            tuan_to = int(self.var_delete_tuan_to.get())
        except (tk.TclError, ValueError):
            messagebox.showwarning("Cảnh báo", "Khoảng tuần không hợp lệ.")
            return
        if tuan_from < 1 or tuan_to < 1 or tuan_from > 52 or tuan_to > 52:
            messagebox.showwarning("Cảnh báo", "Tuần phải nằm trong khoảng 1–52.")
            return
        if tuan_from > tuan_to:
            tuan_from, tuan_to = tuan_to, tuan_from
        self.var_delete_tuan_from.set(tuan_from)
        self.var_delete_tuan_to.set(tuan_to)

        only_mine = bool(self.var_delete_only_mine.get())
        self._delete_scanned_entries = []
        self._delete_scan_signature = None
        self._clear_delete_queue()
        self._delete_scanning = True
        if self._delete_run_button and self._delete_run_button.winfo_exists():
            self._delete_run_button.config(state="disabled")
        if self._delete_scan_button and self._delete_scan_button.winfo_exists():
            self._delete_scan_button.config(state="disabled")
        self._set_delete_status(
            f"⏳ Đang quét lớp {lop_text} | Tuần {tuan_from}→{tuan_to}...", "#a86400"
        )

        params = {
            "port": self._cdp_port,
            "lop": lop_text,
            "tuan_from": tuan_from,
            "tuan_to": tuan_to,
            "only_mine": only_mine,
            "signature": self._compute_delete_scan_signature(
                lop_text, tuan_from, tuan_to, only_mine
            ),
        }
        self._delete_thread = threading.Thread(
            target=self._delete_scan_worker, args=(params,), daemon=True
        )
        self._delete_thread.start()
        self.root.after(120, self._poll_delete_queue)

    def _delete_scan_worker(self, params):
        """Worker quét tiết đã ghi để preview (read-only)."""
        q = self._delete_queue
        bridge = None
        try:
            bridge = ChromeBridge(port=params["port"])
            ok, msg = bridge.connect()
            if not ok:
                q.put(("scan_error", f"Kết nối CDP thất bại: {msg}"))
                return

            teacher_name = ""
            if params.get("only_mine"):
                ok_user, name_or_err = bridge.get_current_user_full_name()
                if ok_user:
                    teacher_name = str(name_or_err)
                else:
                    q.put(("scan_warn",
                           f"Không đọc được tên giáo viên hiện tại ({name_or_err}). "
                           "Bộ lọc 'chỉ xóa tiết của tôi' sẽ không áp dụng được."))
            teacher_key = self._normalize_person_name(teacher_name) if teacher_name else ""

            all_entries = []
            week_errors = []
            for tuan_num in range(int(params["tuan_from"]), int(params["tuan_to"]) + 1):
                q.put(("scan_status", f"⏳ Quét lớp {params['lop']} — Tuần {tuan_num}..."))
                ok_fetch, payload = bridge.fetch_deletable_entries(
                    params["lop"], tuan_num, timeout_s=12.0
                )
                if not ok_fetch:
                    week_errors.append({"week": tuan_num, "message": str(payload)})
                    continue
                server_week = payload.get("week")
                if server_week is not None and int(server_week) != int(tuan_num):
                    week_errors.append({
                        "week": tuan_num,
                        "message": f"Service trả về tuần {server_week}, không khớp tuần {tuan_num}",
                    })
                    continue
                for entry in payload.get("entries", []):
                    item = dict(entry)
                    item["week"] = tuan_num
                    item["week_text"] = f"Tuần {tuan_num}"
                    is_mine = bool(
                        teacher_key and
                        self._normalize_person_name(item.get("ky_ten", "")) == teacher_key
                    )
                    item["is_mine"] = is_mine
                    all_entries.append(item)

            q.put(("scan_done", {
                "entries": all_entries,
                "teacher_name": teacher_name,
                "only_mine": bool(params.get("only_mine")),
                "lop": params["lop"],
                "tuan_from": int(params["tuan_from"]),
                "tuan_to": int(params["tuan_to"]),
                "week_errors": week_errors,
                "signature": params.get("signature"),
            }))
        except Exception as e:
            q.put(("scan_error", f"Worker quét xóa lỗi: {type(e).__name__}: {str(e)[:140]}"))
        finally:
            if bridge:
                try:
                    bridge.disconnect()
                except Exception:
                    pass

    def _render_delete_preview(self, payload):
        """Hiển thị preview các tiết sẽ xóa và bật nút xóa nếu hợp lệ."""
        widget = self._delete_result_text
        if widget is None or not widget.winfo_exists():
            return

        only_mine = bool(payload.get("only_mine"))
        teacher_name = str(payload.get("teacher_name", "")).strip()
        all_entries = list(payload.get("entries", []))
        week_errors = list(payload.get("week_errors", []))

        if only_mine and teacher_name:
            target = [e for e in all_entries if e.get("is_mine")]
            others = [e for e in all_entries if not e.get("is_mine")]
        elif only_mine and not teacher_name:
            # An toàn: bật lọc "chỉ của tôi" nhưng KHÔNG đọc được tên GV →
            # không xác định được tiết nào của mình → KHÔNG xóa gì cả.
            target = []
            others = list(all_entries)
        else:
            target = list(all_entries)
            others = []

        self._delete_scanned_entries = target
        self._delete_scan_signature = payload.get("signature")

        widget.config(state="normal")
        widget.delete("1.0", "end")

        widget.insert("end", f"Lớp {payload.get('lop','?')} | "
                             f"Tuần {payload.get('tuan_from','?')}→{payload.get('tuan_to','?')}\n", "title")
        if only_mine:
            widget.insert("end", f"Bộ lọc: chỉ tiết của '{teacher_name or '(không rõ)'}'\n", "muted")
        else:
            widget.insert("end", "Bộ lọc: XÓA TẤT CẢ giáo viên (cẩn thận!)\n", "warn")
        widget.insert("end", f"Số tiết SẼ XÓA: {len(target)}", "title")
        if others:
            widget.insert("end", f"  |  Bỏ qua (GV khác): {len(others)}", "muted")
        widget.insert("end", "\n\n")

        if not target:
            widget.insert("end", "Không có tiết nào khớp điều kiện để xóa.\n", "warn")
        else:
            for e in target:
                widget.insert(
                    "end",
                    f"[XÓA] {e.get('week_text','')} | Thứ {e.get('thu','?')} "
                    f"{e.get('buoi','?')} Tiết {e.get('tiet','?')} | PPCT {e.get('ppct','--')} | "
                    f"{e.get('mon_hoc','')} | {e.get('ky_ten','')} | id={e.get('chitiet_id','')}\n",
                    "error",
                )

        if others:
            widget.insert("end", "\nCác tiết của giáo viên khác (KHÔNG xóa):\n", "title")
            for e in others[:50]:
                widget.insert(
                    "end",
                    f"  - {e.get('week_text','')} | Thứ {e.get('thu','?')} {e.get('buoi','?')} "
                    f"Tiết {e.get('tiet','?')} | {e.get('mon_hoc','')} | {e.get('ky_ten','')}\n",
                    "muted",
                )
            if len(others) > 50:
                widget.insert("end", f"  ... còn {len(others) - 50} tiết khác\n", "muted")

        if week_errors:
            widget.insert("end", "\nTuần đọc lỗi:\n", "title")
            for item in week_errors[:10]:
                widget.insert("end", f"  - Tuần {item['week']}: {item['message']}\n", "warn")

        widget.config(state="disabled")

        self._delete_scanning = False
        if self._delete_run_button and self._delete_run_button.winfo_exists():
            self._delete_run_button.config(state="normal" if target else "disabled")
        if self._delete_scan_button and self._delete_scan_button.winfo_exists():
            self._delete_scan_button.config(state="normal")

        if target:
            self._set_delete_status(
                f"Sẵn sàng xóa {len(target)} tiết. Gõ 'XOA' rồi bấm nút xóa.", "#b00020"
            )
        elif only_mine and not teacher_name:
            self._set_delete_status(
                "Không đọc được tên giáo viên → không thể xác định tiết của bạn. "
                "Hãy đăng nhập lại hoặc bỏ chọn lọc (rủi ro) rồi Quét lại.",
                "#b00020",
            )
        else:
            self._set_delete_status("Không có tiết nào để xóa.", "#1f7a1f")

    def _on_delete_run(self):
        """Thực thi xóa các tiết đã quét (sau khi xác nhận)."""
        if self._delete_running or self._delete_scanning:
            return
        if not self._cdp_connected:
            self._set_delete_status("Chưa kết nối CDP!", "#b00020")
            return
        busy = self._delete_busy_reason()
        if busy:
            self._set_delete_status(busy, "#b00020")
            messagebox.showwarning("Cảnh báo", busy)
            return
        target = list(self._delete_scanned_entries or [])
        if not target:
            messagebox.showinfo("Thông báo", "Chưa có tiết nào để xóa. Hãy Quét trước.")
            return

        # Chống xóa lệch: ngữ cảnh hiện tại phải khớp đúng lần Quét gần nhất.
        current_sig = self._current_delete_signature()
        if self._delete_scan_signature is None or current_sig != self._delete_scan_signature:
            self._invalidate_delete_scan(
                "Lựa chọn đã thay đổi sau lần Quét. Hãy bấm Quét lại trước khi xóa."
            )
            messagebox.showwarning(
                "Cần quét lại",
                "Lớp / khoảng Tuần / bộ lọc đã thay đổi so với lần Quét gần nhất.\n"
                "Hãy bấm 'Quét tiết đã ghi' lại để xác nhận danh sách trước khi xóa.",
            )
            return

        confirm_text = self._normalize_person_name(self.var_delete_confirm.get())
        if confirm_text != "xoa":
            messagebox.showwarning(
                "Xác nhận chưa đúng",
                "Hãy gõ chính xác chữ XOA vào ô xác nhận trước khi xóa.",
            )
            return

        # Lọc lại các entry có chitiet_id hợp lệ ngay tại đây để con số xác nhận
        # khớp đúng số sẽ thực sự gửi lệnh xóa.
        valid_target = [
            e for e in target if str(e.get("chitiet_id", "")).strip()
        ]
        if not valid_target:
            messagebox.showinfo("Thông báo", "Không có tiết nào có mã hợp lệ để xóa.")
            return

        if not messagebox.askyesno(
            "XÁC NHẬN XÓA",
            f"Xóa vĩnh viễn {len(valid_target)} tiết đã ghi?\n"
            f"Lớp {self.var_delete_lop.get().strip()} | "
            f"Tuần {self.var_delete_tuan_from.get()}→{self.var_delete_tuan_to.get()}\n"
            "Thao tác này KHÔNG THỂ HOÀN TÁC.",
            icon="warning",
        ):
            return

        self.var_delete_confirm.set("")
        self._clear_delete_queue()
        self._delete_stop_event.clear()
        self._set_delete_running(True)
        self._set_delete_status(f"⏳ Đang xóa {len(valid_target)} tiết...", "#a86400")
        self._delete_log(f"\n=== BẮT ĐẦU XÓA {len(valid_target)} tiết ===", "title")

        params = {
            "port": self._cdp_port,
            # Signature đã đảm bảo lớp hiện tại khớp đúng lớp lúc Quét (case-insensitive),
            # nên dùng giá trị hiển thị đúng hoa/thường cho verify refetch.
            "lop": self.var_delete_lop.get().strip(),
            "entries": copy.deepcopy(valid_target),
        }
        self._delete_thread = threading.Thread(
            target=self._delete_run_worker, args=(params,), daemon=True
        )
        self._delete_thread.start()
        self.root.after(120, self._poll_delete_queue)

    def _on_delete_stop(self):
        """Yêu cầu dừng tiến trình xóa sau tiết hiện tại."""
        if self._delete_running and not self._delete_stop_event.is_set():
            self._delete_stop_event.set()
            self._set_delete_status("⏸ Đang dừng sau tiết hiện tại...", "#a86400")
            if self._delete_stop_button and self._delete_stop_button.winfo_exists():
                self._delete_stop_button.config(state="disabled")

    def _delete_run_worker(self, params):
        """Worker xóa từng tiết qua API + verify refetch (đã trống)."""
        q = self._delete_queue
        bridge = None
        deleted = 0
        failed = 0
        skipped = 0
        stopped = False
        lop_text = params["lop"]
        try:
            bridge = ChromeBridge(port=params["port"])
            ok, msg = bridge.connect()
            if not ok:
                q.put(("run_error", f"Kết nối CDP thất bại: {msg}"))
                return

            # Gom entries theo tuần để verify theo tuần (1 fetch/tuần sau khi xóa)
            entries = list(params.get("entries", []))
            weeks_touched = set()
            deleted_ok_ids = set()  # CHỈ các id đã xóa thành công (để verify đúng)

            for idx, entry in enumerate(entries):
                if self._delete_stop_event.is_set():
                    stopped = True
                    break

                chitiet_id = str(entry.get("chitiet_id", "")).strip()
                label = (
                    f"{entry.get('week_text','')} | Thứ {entry.get('thu','?')} "
                    f"{entry.get('buoi','?')} Tiết {entry.get('tiet','?')} | "
                    f"{entry.get('mon_hoc','')} (id={chitiet_id})"
                )
                if not chitiet_id:
                    skipped += 1
                    q.put(("run_log", f"[BỎ QUA] {label}: thiếu chitiet_id", "warn"))
                    continue

                q.put(("run_status", f"⏳ Đang xóa {idx + 1}/{len(entries)}: {label}"))
                ok_del, msg_del = bridge.delete_entry_by_id(chitiet_id, timeout_s=12.0)
                if ok_del:
                    deleted += 1
                    deleted_ok_ids.add(chitiet_id)
                    weeks_touched.add(int(entry.get("week", 0) or 0))
                    q.put(("run_log", f"[OK] {label}", "ok"))
                else:
                    failed += 1
                    q.put(("run_log", f"[LỖI] {label}: {msg_del}", "error"))

            # Verify: refetch các tuần đã đụng tới, chỉ kiểm tra các id ĐÃ xóa OK.
            # Nếu id còn xuất hiện -> xóa chưa ăn (rollback/đồng bộ trễ).
            remaining = 0
            verify_errors = []
            for week_num in sorted(w for w in weeks_touched if w > 0):
                if not deleted_ok_ids:
                    break
                ok_fetch, payload = bridge.fetch_deletable_entries(
                    lop_text, week_num, timeout_s=12.0
                )
                if not ok_fetch:
                    verify_errors.append({"week": week_num, "message": str(payload)})
                    continue
                # Chống alias: service phải trả đúng tuần mới tin kết quả verify.
                server_week = payload.get("week")
                if server_week is not None and int(server_week) != int(week_num):
                    verify_errors.append({
                        "week": week_num,
                        "message": f"Service trả về tuần {server_week} khi verify, bỏ qua",
                    })
                    continue
                still = {
                    str(e.get("chitiet_id", "")).strip()
                    for e in payload.get("entries", [])
                }
                left = deleted_ok_ids & still
                if left:
                    remaining += len(left)
                    q.put(("run_log",
                           f"[CẢNH BÁO] Tuần {week_num} còn {len(left)} tiết chưa xóa được: "
                           + ", ".join(sorted(left)), "warn"))

            q.put(("run_done", {
                "deleted": deleted,
                "failed": failed,
                "skipped": skipped,
                "stopped": stopped,
                "remaining": remaining,
                "verify_errors": verify_errors,
            }))
        except Exception as e:
            q.put(("run_error", f"Worker xóa lỗi: {type(e).__name__}: {str(e)[:140]}"))
        finally:
            if bridge:
                try:
                    bridge.disconnect()
                except Exception:
                    pass

    def _poll_delete_queue(self):
        """Poll queue cho dialog xóa."""
        try:
            while not self._delete_queue.empty():
                msg = self._delete_queue.get_nowait()
                mtype = msg[0]

                if mtype == "scan_status":
                    self._set_delete_status(msg[1], "#a86400")
                elif mtype == "scan_warn":
                    self._delete_log(msg[1], "warn")
                elif mtype == "scan_error":
                    self._set_delete_status(msg[1], "#b00020")
                    self._delete_log(msg[1], "error")
                    self._delete_scanning = False
                    if self._delete_scan_button and self._delete_scan_button.winfo_exists():
                        self._delete_scan_button.config(state="normal")
                    return
                elif mtype == "scan_done":
                    self._render_delete_preview(msg[1])
                    return
                elif mtype == "run_status":
                    self._set_delete_status(msg[1], "#a86400")
                elif mtype == "run_log":
                    self._delete_log(msg[1], msg[2] if len(msg) > 2 else "muted")
                elif mtype == "run_error":
                    self._set_delete_status(msg[1], "#b00020")
                    self._delete_log(msg[1], "error")
                    self._set_delete_running(False)
                    return
                elif mtype == "run_done":
                    self._delete_finished(msg[1])
                    return
        except Exception as e:
            self._set_delete_status(f"Lỗi poll xóa: {e}", "#b00020")
            self._delete_scanning = False
            self._set_delete_running(False)
            return

        if (self._delete_running or (self._delete_thread and self._delete_thread.is_alive())) \
                and self._root_exists():
            self.root.after(150, self._poll_delete_queue)

    def _delete_finished(self, summary):
        """Xử lý kết thúc tiến trình xóa."""
        deleted = int(summary.get("deleted", 0) or 0)
        failed = int(summary.get("failed", 0) or 0)
        skipped = int(summary.get("skipped", 0) or 0)
        stopped = bool(summary.get("stopped"))
        remaining = int(summary.get("remaining", 0) or 0)

        self._set_delete_running(False)
        self._delete_scanned_entries = []
        # Danh sách vừa quét đã cũ (các id vừa xóa không còn tồn tại) → buộc quét lại.
        self._delete_scan_signature = None

        prefix = "⏸ Đã dừng" if stopped else "🏁 Hoàn tất"
        color = "#b00020" if (failed or remaining) else "#1f7a1f"
        status = (
            f"{prefix}: đã xóa {deleted}, lỗi {failed}, bỏ qua {skipped}"
            + (f", còn sót {remaining}" if remaining else "")
        )
        self._set_delete_status(status, color)
        self._delete_log(f"=== {status} ===", "title")
        self._log(
            f"🗑 Xóa sổ đầu bài: đã xóa {deleted}, lỗi {failed}, bỏ qua {skipped}"
            + (f", còn sót {remaining}" if remaining else ""),
            "success" if (not failed and not remaining) else "warning",
        )

        if deleted > 0:
            self._invalidate_class_stats_cache()
            self._on_sched_progress_context_changed()
        # Tự refresh preview để phản ánh trạng thái mới — chỉ khi dialog còn mở,
        # không bị dừng, và không có thao tác nền khác đang chạy.
        if (
            not stopped
            and deleted > 0
            and self._delete_dialog
            and self._delete_dialog.winfo_exists()
            and not self._delete_busy_reason()
        ):
            self.root.after(400, self._on_delete_scan)

    def _set_class_stats_running(self, running, selected_lop=None):
        """Bật/tắt trạng thái chạy cho cụm thống kê lớp."""
        self._class_stats_running = running
        self._class_stats_selected_lop = selected_lop
        for lop_text, btn in self._class_stats_buttons.items():
            btn.config(state="disabled" if running else "normal")
        if getattr(self, "btn_stats_all_classes", None) is not None:
            try:
                self.btn_stats_all_classes.config(state="disabled" if running else "normal")
            except Exception:
                pass
        if getattr(self, "btn_stats_all_missing", None) is not None:
            try:
                self.btn_stats_all_missing.config(state="disabled" if running else "normal")
            except Exception:
                pass
        if getattr(self, "btn_stats_missing_teachers", None) is not None:
            try:
                self.btn_stats_missing_teachers.config(state="disabled" if running else "normal")
            except Exception:
                pass
        if getattr(self, "btn_stats_khdh_pending", None) is not None:
            try:
                self.btn_stats_khdh_pending.config(state="disabled" if running else "normal")
            except Exception:
                pass
        if getattr(self, "cmb_stats_mon_hoc", None) is not None:
            try:
                self.cmb_stats_mon_hoc.config(state="disabled" if running else "readonly")
            except Exception:
                pass

    def _get_class_stats_request_context(self, require_subject=True):
        """Đọc và validate context chung cho mọi thao tác thống kê lớp."""
        if not self._cdp_connected:
            self._log("Chưa kết nối CDP!", "error")
            return None
        if self._schedule_running:
            messagebox.showwarning(
                "Cảnh báo",
                "Schedule đang chạy. Hãy dừng hoặc chờ xong rồi mới thống kê lớp.",
            )
            return None
        if self._delete_running or self._delete_scanning:
            messagebox.showwarning(
                "Cảnh báo",
                "Đang có thao tác Xóa dữ liệu chạy. Hãy chờ xong rồi mới thống kê lớp.",
            )
            return None
        if self._class_stats_running:
            self._log("Đang có một phiên thống kê lớp chạy rồi.", "warning")
            return None
        if self._class_stats_thread and self._class_stats_thread.is_alive():
            self._log("Đang có một tác vụ thống kê lớp khác đang khởi tạo.", "warning")
            return None

        mon_hoc_opt = None
        if require_subject:
            mon_hoc_opt = self._resolve_sched_option(
                self._class_stats_mon_hoc_options,
                self.var_stats_mon_hoc.get(),
            )
        if require_subject and mon_hoc_opt is None:
            messagebox.showwarning(
                "Cảnh báo",
                "Hãy chọn Môn học cần thống kê trước khi bấm vào lớp.",
            )
            return None

        try:
            tuan_from = int(self.var_stats_tuan_from.get())
            tuan_to = int(self.var_stats_tuan_to.get())
        except (tk.TclError, ValueError):
            messagebox.showwarning("Cảnh báo", "Tuần thống kê không hợp lệ.")
            return None
        if tuan_from > tuan_to:
            tuan_from, tuan_to = tuan_to, tuan_from

        self.var_stats_tuan_from.set(tuan_from)
        self.var_stats_tuan_to.set(tuan_to)
        return {
            "tuan_from": tuan_from,
            "tuan_to": tuan_to,
            "mon_hoc": mon_hoc_opt["text"] if mon_hoc_opt else "",
        }

    def _on_run_class_stats(self, lop_text):
        """Phân tích dữ liệu PPCT cho một lớp trong khoảng tuần được chọn."""
        context = self._get_class_stats_request_context()
        if context is None:
            return

        self._clear_class_stats_queue()
        self._set_class_stats_running(True, lop_text)
        self._set_class_stats_status(
            f"⏳ Đang quét lớp {lop_text} | Môn {context['mon_hoc']} | "
            f"Tuần {context['tuan_from']}→{context['tuan_to']}...",
            "#a86400",
        )
        self._log(
            f"📊 Thống kê lớp bắt đầu: {lop_text}, Môn {context['mon_hoc']}, "
            f"Tuần {context['tuan_from']}→{context['tuan_to']}",
            "info",
        )

        record_map = self._get_class_stats_record_map()
        params = {
            "port": self._cdp_port,
            "lop": lop_text,
            "class_meta": copy.deepcopy(record_map.get(str(lop_text).strip().lower()) or {}),
            "tuan_from": context["tuan_from"],
            "tuan_to": context["tuan_to"],
            "mon_hoc": context["mon_hoc"],
            "scan_mode": "fast_max",
        }
        self._class_stats_thread = threading.Thread(
            target=self._class_stats_worker,
            args=(params,),
            daemon=True,
        )
        self._class_stats_thread.start()
        self.root.after(100, self._poll_class_stats_queue)

    def _on_run_all_class_stats(self):
        """Tổng hợp nhanh PPCT cao nhất cho toàn bộ lớp đang có theo môn đã chọn."""
        context = self._get_class_stats_request_context()
        if context is None:
            return
        lop_options = [x for x in self._class_stats_lop_options if str(x).strip()]
        if not lop_options:
            messagebox.showwarning(
                "Cảnh báo",
                "Chưa có danh sách lớp. Hãy bấm tải danh sách lớp trước.",
            )
            return

        self._clear_class_stats_queue()
        self._set_class_stats_running(True, "ALL_CLASSES")
        self._set_class_stats_status(
            f"⚡ Đang quét nhanh {len(lop_options)} lớp | Môn {context['mon_hoc']} | "
            f"Tuần {context['tuan_from']}→{context['tuan_to']}...",
            "#a86400",
        )
        self._log(
            f"⚡ Bắt đầu quét nhanh PPCT cao nhất: {len(lop_options)} lớp, "
            f"Môn {context['mon_hoc']}, Tuần {context['tuan_from']}→{context['tuan_to']}",
            "info",
        )

        record_map = self._get_class_stats_record_map()
        params = {
            "port": self._cdp_port,
            "lop_list": lop_options,
            "class_records": [
                copy.deepcopy(record_map.get(str(lop).strip().lower()) or {"text": str(lop).strip()})
                for lop in lop_options
            ],
            "tuan_from": context["tuan_from"],
            "tuan_to": context["tuan_to"],
            "mon_hoc": context["mon_hoc"],
        }
        self._class_stats_thread = threading.Thread(
            target=self._class_stats_all_worker,
            args=(params,),
            daemon=True,
        )
        self._class_stats_thread.start()
        self.root.after(100, self._poll_class_stats_queue)

    def _on_run_all_class_missing(self):
        """Quét FULL toàn bộ lớp để phát hiện lớp thiếu/trùng PPCT trong khoảng tuần."""
        context = self._get_class_stats_request_context()
        if context is None:
            return
        lop_options = [x for x in self._class_stats_lop_options if str(x).strip()]
        if not lop_options:
            messagebox.showwarning(
                "Cảnh báo",
                "Chưa có danh sách lớp. Hãy bấm tải danh sách lớp trước.",
            )
            return

        self._clear_class_stats_queue()
        self._set_class_stats_running(True, "ALL_CLASSES_FULL")
        self._set_class_stats_status(
            f"🔎 Đang quét FULL {len(lop_options)} lớp | Môn {context['mon_hoc']} | "
            f"Tuần {context['tuan_from']}→{context['tuan_to']}...",
            "#a86400",
        )
        self._log(
            f"🔎 Bắt đầu quét thiếu PPCT toàn lớp: {len(lop_options)} lớp, "
            f"Môn {context['mon_hoc']}, Tuần {context['tuan_from']}→{context['tuan_to']}",
            "info",
        )

        record_map = self._get_class_stats_record_map()
        params = {
            "port": self._cdp_port,
            "lop_list": lop_options,
            "class_records": [
                copy.deepcopy(record_map.get(str(lop).strip().lower()) or {"text": str(lop).strip()})
                for lop in lop_options
            ],
            "tuan_from": context["tuan_from"],
            "tuan_to": context["tuan_to"],
            "mon_hoc": context["mon_hoc"],
            "scan_mode": "full_missing",
        }
        self._class_stats_thread = threading.Thread(
            target=self._class_stats_all_worker,
            args=(params,),
            daemon=True,
        )
        self._class_stats_thread.start()
        self.root.after(100, self._poll_class_stats_queue)

    @staticmethod
    def _infer_grade_label_from_class_text(lop_text):
        """Suy ra nhãn 'Khối X' từ tên lớp như 6A4."""
        match = re.match(r"\s*(\d{1,2})", str(lop_text or "").strip())
        if not match:
            return ""
        return f"Khối {int(match.group(1))}"

    def _get_class_stats_record_map(self):
        """Map text lớp -> metadata class_id/khoi/cap đã quét được."""
        record_map = {}
        for item in list(self._class_stats_lop_records or []):
            text = str(item.get("text", "") or "").strip()
            if not text:
                continue
            key = text.lower()
            current = record_map.get(key, {"text": text, "value": "", "khoi": "", "cap": ""})
            for field in ("value", "khoi", "cap", "source"):
                incoming = str(item.get(field, "") or "").strip()
                if incoming and not current.get(field):
                    current[field] = incoming
            if not current.get("khoi"):
                current["khoi"] = re.sub(r"^Khối\s*", "", self._infer_grade_label_from_class_text(text), flags=re.I)
            record_map[key] = current
        return record_map

    def _resolve_missing_teacher_slots(self, stats_rows, detail_rows):
        """Map dữ liệu thiếu GV theo bucket Thứ/Buổi sang các tiết trống trong bảng chi tiết."""
        bucket_empty = {}
        detail_rows = list(detail_rows or [])
        has_schedule_classification = any("is_scheduled" in row for row in detail_rows)
        for row in detail_rows:
            if row.get("has_data"):
                continue
            if has_schedule_classification and not row.get("is_scheduled"):
                continue
            tiet_text = str(row.get("tiet", "") or "").strip()
            if not tiet_text.isdigit():
                continue
            thu_key = ChromeBridge._normalize_thu_token(row.get("thu", ""))
            buoi_key = ChromeBridge._normalize_buoi_token(row.get("buoi", ""))
            if not thu_key or not buoi_key:
                continue
            bucket_empty.setdefault((thu_key, buoi_key), []).append({
                "thu": str(row.get("thu", "") or "").strip(),
                "buoi": str(row.get("buoi", "") or "").strip(),
                "tiet": tiet_text,
                "slot_label": self._format_schedule_slot_label(
                    row.get("thu", "?"),
                    row.get("buoi", "?"),
                    row.get("tiet", "?"),
                ),
            })

        for rows in bucket_empty.values():
            rows.sort(key=lambda item: int(item.get("tiet", 0) or 0))

        resolved = []
        bucket_demands = {}
        for idx, stats_row in enumerate(list(stats_rows or [])):
            teacher_payload = {
                "teacher_name": str(stats_row.get("teacher_name", "") or "").strip(),
                "mon_hoc": str(stats_row.get("mon_hoc", "") or "").strip(),
                "lop": str(stats_row.get("lop", "") or "").strip(),
                "total_missing": int(stats_row.get("total_missing", 0) or 0),
                "exact_slots": [],
                "ambiguous_buckets": [],
                "unmatched_buckets": [],
                "raw_counts": copy.deepcopy(list(stats_row.get("counts") or [])),
            }
            resolved.append(teacher_payload)
            for count_info in list(stats_row.get("counts") or []):
                try:
                    count_value = int(count_info.get("count", 0) or 0)
                except Exception:
                    count_value = 0
                if count_value <= 0:
                    continue
                thu = str(count_info.get("thu", "") or "").strip()
                buoi = str(count_info.get("buoi", "") or "").strip()
                key = (
                    ChromeBridge._normalize_thu_token(thu),
                    ChromeBridge._normalize_buoi_token(buoi),
                )
                bucket_demands.setdefault(key, []).append({
                    "row_index": idx,
                    "teacher_name": teacher_payload["teacher_name"],
                    "mon_hoc": teacher_payload["mon_hoc"],
                    "lop": teacher_payload["lop"],
                    "thu": thu,
                    "buoi": buoi,
                    "count": count_value,
                })

        for bucket_key, demands in bucket_demands.items():
            empty_rows = list(bucket_empty.get(bucket_key, []) or [])
            total_demand = sum(int(item.get("count", 0) or 0) for item in demands)
            unique_teachers = {
                (item.get("teacher_name", ""), item.get("mon_hoc", ""))
                for item in demands
            }
            if not empty_rows:
                for demand in demands:
                    resolved[demand["row_index"]]["unmatched_buckets"].append({
                        "thu": demand["thu"],
                        "buoi": demand["buoi"],
                        "count": demand["count"],
                        "note": "Bảng thống kê báo thiếu nhưng không tìm thấy tiết trống tương ứng trong bảng chi tiết.",
                    })
                continue

            if len(demands) == 1 and len(empty_rows) == total_demand:
                demand = demands[0]
                resolved[demand["row_index"]]["exact_slots"].extend(copy.deepcopy(empty_rows))
                continue

            for demand in demands:
                target = resolved[demand["row_index"]]
                target["ambiguous_buckets"].append({
                    "thu": demand["thu"],
                    "buoi": demand["buoi"],
                    "count": demand["count"],
                    "candidate_slots": copy.deepcopy(empty_rows),
                    "note": (
                        "Không phân bổ chắc chắn được tiết vì cùng bucket có nhiều tiết trống "
                        "hoặc nhiều giáo viên thiếu."
                        if len(unique_teachers) > 1 or len(empty_rows) != demand["count"]
                        else "Bucket còn nhiều tiết trống hơn số lượng thiếu."
                    ),
                })

        for item in resolved:
            item["exact_slots"].sort(key=lambda slot: (
                self._schedule_occurrence_sort_key({
                    "week": 0,
                    "thu": slot.get("thu"),
                    "buoi": slot.get("buoi"),
                    "tiet": slot.get("tiet"),
                })[1],
                self._schedule_buoi_sort_key(slot.get("buoi")),
                int(slot.get("tiet", 0) or 0),
            ))
        return resolved

    def _on_run_missing_teacher_audit(self):
        """Quét màn thống kê để tìm giáo viên chưa nhập theo tuần/lớp."""
        context = self._get_class_stats_request_context(require_subject=False)
        if context is None:
            return

        lop_options = [x for x in self._class_stats_lop_options if str(x).strip()]
        if not lop_options:
            messagebox.showwarning(
                "Cảnh báo",
                "Chưa có danh sách lớp. Hãy bấm tải danh sách lớp trước.",
            )
            return

        record_map = self._get_class_stats_record_map()
        class_records = []
        missing_meta = []
        for lop_text in lop_options:
            key = str(lop_text).strip().lower()
            record = copy.deepcopy(record_map.get(key) or {})
            if not record.get("text"):
                record["text"] = str(lop_text).strip()
            if not record.get("value"):
                missing_meta.append(lop_text)
            class_records.append(record)

        if missing_meta:
            messagebox.showwarning(
                "Cảnh báo",
                "Một số lớp chưa có metadata class_id ổn định. Hãy bấm 'Tải lại DS lớp' rồi thử lại.",
            )
            return

        self._clear_class_stats_queue()
        self._set_class_stats_running(True, "MISSING_TEACHERS")
        self._set_class_stats_status(
            f"👤 Đang quét GV chưa nhập | {len(class_records)} lớp | "
            f"Tuần {context['tuan_from']}→{context['tuan_to']}...",
            "#a86400",
        )
        self._log(
            f"👤 Bắt đầu quét GV chưa nhập theo thống kê: {len(class_records)} lớp | "
            f"Tuần {context['tuan_from']}→{context['tuan_to']}",
            "info",
        )

        params = {
            "port": self._cdp_port,
            "class_records": class_records,
            "tuan_from": context["tuan_from"],
            "tuan_to": context["tuan_to"],
            "username": self.var_vnedu_username.get().strip(),
            "password": self.var_vnedu_password.get(),
        }
        self._class_stats_thread = threading.Thread(
            target=self._class_stats_missing_teacher_worker,
            args=(params,),
            daemon=True,
        )
        self._class_stats_thread.start()
        self.root.after(100, self._poll_class_stats_queue)

    def _on_run_khdh_pending_audit(self):
        """Quét row đỏ KHDH còn treo theo lớp/tuần, không nhập dữ liệu."""
        context = self._get_class_stats_request_context(require_subject=False)
        if context is None:
            return

        lop_options = [x for x in self._class_stats_lop_options if str(x).strip()]
        if not lop_options:
            messagebox.showwarning(
                "Cảnh báo",
                "Chưa có danh sách lớp. Hãy bấm tải danh sách lớp trước.",
            )
            return

        record_map = self._get_class_stats_record_map()
        class_records = []
        for lop_text in lop_options:
            key = str(lop_text).strip().lower()
            record = copy.deepcopy(record_map.get(key) or {})
            if not record.get("text"):
                record["text"] = str(lop_text).strip()
            class_records.append(record)

        self._clear_class_stats_queue()
        self._set_class_stats_running(True, "KHDH_PENDING")
        self._set_class_stats_status(
            f"📝 Đang quét row đỏ KHBD/KHDH | {len(lop_options)} lớp | "
            f"Tuần {context['tuan_from']}→{context['tuan_to']}...",
            "#a86400",
        )
        self._log(
            f"📝 Bắt đầu quét row đỏ KHBD/KHDH: {len(lop_options)} lớp | "
            f"Tuần {context['tuan_from']}→{context['tuan_to']}",
            "info",
        )

        params = {
            "port": self._cdp_port,
            "lop_list": lop_options,
            "class_records": class_records,
            "tuan_from": context["tuan_from"],
            "tuan_to": context["tuan_to"],
            "username": self.var_vnedu_username.get().strip(),
            "password": self.var_vnedu_password.get(),
        }
        self._class_stats_thread = threading.Thread(
            target=self._class_stats_khdh_pending_worker,
            args=(params,),
            daemon=True,
        )
        self._class_stats_thread.start()
        self.root.after(100, self._poll_class_stats_queue)

    def _class_stats_missing_teacher_worker(self, params):
        """Worker quét màn thống kê GV chưa nhập rồi map sang tiết trống."""
        q = self._class_stats_queue
        bridge = None
        reports = []
        issues = []
        pairs_scanned = 0
        pairs_with_missing = 0
        try:
            class_records = list(params.get("class_records") or [])
            bridge = ChromeBridge(port=params["port"])
            ok, msg = bridge.connect()
            if not ok:
                q.put(("stats_error", f"Kết nối CDP thất bại: {msg}"))
                return

            ok_stats, stats_msg = bridge.ensure_thong_ke_nhap_sodau_bai(
                params.get("username", ""),
                params.get("password", ""),
            )
            if not ok_stats:
                q.put(("stats_error", stats_msg))
                return

            class_records_by_grade = {}
            permission_scope_signatures = set()
            for class_record in class_records:
                lop_text = str(class_record.get("text", "") or "").strip()
                if not lop_text:
                    continue
                grade_text = (
                    f"Khối {class_record.get('khoi')}".strip()
                    if str(class_record.get("khoi", "") or "").strip()
                    else self._infer_grade_label_from_class_text(lop_text)
                )
                class_records_by_grade.setdefault(grade_text.casefold(), {
                    "label": grade_text,
                    "records": [],
                })["records"].append(class_record)

            for tuan_num in range(int(params["tuan_from"]), int(params["tuan_to"]) + 1):
                q.put(("stats_status", f"👤 Đang đọc thống kê GV chưa nhập — Tuần {tuan_num}..."))
                ok_week, week_msg = bridge.stats_select_week(f"Tuần {tuan_num}")
                if not ok_week:
                    issues.append({
                        "week": tuan_num,
                        "lop": "",
                        "message": f"Không chọn được tuần: {week_msg}",
                    })
                    continue

                ok_grades, grades_or_error = bridge.stats_get_filter_options("grade")
                if not ok_grades:
                    issues.append({
                        "week": tuan_num,
                        "lop": "",
                        "message": f"Không đọc được khối được cấp quyền: {grades_or_error}",
                    })
                    continue
                available_grades = {
                    str(item or "").strip().casefold(): str(item or "").strip()
                    for item in list(grades_or_error or [])
                    if str(item or "").strip()
                }
                skipped_grades = [
                    info["label"]
                    for key, info in class_records_by_grade.items()
                    if key not in available_grades
                ]
                if skipped_grades:
                    scope_signature = tuple(sorted(skipped_grades))
                    if scope_signature not in permission_scope_signatures:
                        permission_scope_signatures.add(scope_signature)
                        q.put((
                            "log",
                            "Màn thống kê VnEdu không cấp quyền các khối: "
                            + ", ".join(skipped_grades),
                            "warning",
                        ))

                for grade_key, grade_info in class_records_by_grade.items():
                    grade_text = available_grades.get(grade_key)
                    if not grade_text:
                        continue
                    ok_grade, grade_msg = bridge.stats_select_grade(grade_text)
                    if not ok_grade:
                        issues.append({
                            "week": tuan_num,
                            "lop": "",
                            "message": f"Không chọn được {grade_text}: {grade_msg}",
                        })
                        continue
                    ok_classes, classes_or_error = bridge.stats_get_filter_options("class")
                    if not ok_classes:
                        issues.append({
                            "week": tuan_num,
                            "lop": "",
                            "message": (
                                f"Không đọc được lớp được cấp quyền của {grade_text}: "
                                f"{classes_or_error}"
                            ),
                        })
                        continue
                    available_classes = {
                        str(item or "").strip().casefold(): str(item or "").strip()
                        for item in list(classes_or_error or [])
                        if str(item or "").strip()
                    }

                    for class_record in list(grade_info["records"] or []):
                        requested_lop = str(class_record.get("text", "") or "").strip()
                        lop_text = available_classes.get(requested_lop.casefold())
                        if not lop_text:
                            continue
                        q.put((
                            "stats_status",
                            f"👤 Tuần {tuan_num}: {lop_text} | {grade_text}...",
                        ))
                        ok_class, class_msg = bridge.stats_select_class(lop_text)
                        if not ok_class:
                            issues.append({
                                "week": tuan_num,
                                "lop": lop_text,
                                "message": f"Không chọn được lớp: {class_msg}",
                            })
                            continue
                        ok_toggle, toggle_msg = bridge.stats_set_missing_only(True)
                        if not ok_toggle:
                            issues.append({
                                "week": tuan_num,
                                "lop": lop_text,
                                "message": f"Không bật được lọc GV chưa nhập: {toggle_msg}",
                            })
                            continue

                        ok_stats_rows, stats_payload = bridge.stats_read_missing_teacher_rows(
                            expected_class=lop_text,
                            timeout_s=6.5,
                        )
                        pairs_scanned += 1
                        if not ok_stats_rows:
                            issues.append({
                                "week": tuan_num,
                                "lop": lop_text,
                                "message": str(stats_payload),
                            })
                            continue

                        stats_rows = list((stats_payload or {}).get("rows") or [])
                        if not stats_rows:
                            continue

                        ok_detail, detail_payload = bridge.fetch_sodaubai_rows(
                            requested_lop,
                            tuan_num,
                            timeout_s=9.5,
                            class_meta=class_record,
                            show_goi_y=True,
                        )
                        if not ok_detail:
                            issues.append({
                                "week": tuan_num,
                                "lop": lop_text,
                                "message": f"Không fetch được bảng chi tiết: {detail_payload}",
                            })
                            continue

                        pairs_with_missing += 1
                        resolved_rows = self._resolve_missing_teacher_slots(
                            stats_rows,
                            list((detail_payload or {}).get("rows") or []),
                        )
                        reports.append({
                            "week": tuan_num,
                            "week_text": f"Tuần {tuan_num}",
                            "lop": lop_text,
                            "teachers": resolved_rows,
                        })

            q.put(("missing_teacher_done", {
                "tuan_from": int(params["tuan_from"]),
                "tuan_to": int(params["tuan_to"]),
                "reports": reports,
                "issues": issues,
                "pairs_scanned": pairs_scanned,
                "pairs_with_missing": pairs_with_missing,
                "class_count": len(class_records),
            }))
        except Exception as e:
            q.put(("stats_error", f"Worker GV chưa nhập lỗi: {type(e).__name__}: {str(e)[:160]}"))
        finally:
            if bridge:
                try:
                    ok_cleanup, msg_cleanup = bridge.cleanup_after_automation(restore_view_mode=False)
                    q.put(("stats_status", f"Đã cleanup trạng thái web sau thống kê: {msg_cleanup}" if ok_cleanup else msg_cleanup))
                except Exception:
                    pass
                try:
                    bridge.disconnect()
                except Exception:
                    pass

    def _class_stats_khdh_pending_worker(self, params):
        """Worker quét KHDH chưa nhập bằng service; UI chỉ là fallback."""
        q = self._class_stats_queue
        bridge = None
        reports = []
        issues = []
        pairs_scanned = 0
        pairs_with_pending = 0
        total_rows = 0
        skipped_unavailable = 0
        try:
            lop_list = [
                str(item or "").strip()
                for item in list(params.get("lop_list") or [])
                if str(item or "").strip()
            ]
            requested_lop_keys = {item.casefold(): item for item in lop_list}
            week_numbers = list(range(
                int(params["tuan_from"]),
                int(params["tuan_to"]) + 1,
            ))
            record_map = {}
            for item in list(params.get("class_records") or []):
                text = str(item.get("text", "") or "").strip()
                if text:
                    record_map[text.casefold()] = copy.deepcopy(item)
            for lop_text in lop_list:
                record_map.setdefault(lop_text.casefold(), {"text": lop_text})

            def _fatal_if_cdp_closed(message):
                if is_cdp_target_closed_error(message):
                    q.put(("stats_error", (
                        "CDP/Chrome đã đóng hoặc tab VnEdu không còn khả dụng. "
                        "Worker đã dừng để tránh sinh lỗi lặp. Hãy mở lại/kết nối lại Chrome rồi chạy lại."
                    )))
                    return True
                return False

            def _select_dropdown_retry(label, text, attempts=3):
                last_msg = ""
                for attempt in range(max(int(attempts), 1)):
                    ok_select, msg_select = bridge.select_dropdown(label, text)
                    if ok_select:
                        return True, msg_select
                    last_msg = str(msg_select)
                    if is_cdp_target_closed_error(last_msg):
                        return False, last_msg
                    if attempt < attempts - 1:
                        time.sleep(0.35 + attempt * 0.35)
                return False, last_msg

            def _normalize_pending_rows(rows, require_schedule_flag):
                pending_rows = []
                for row in list(rows or []):
                    if row.get("has_data") or not row.get("has_add_btn"):
                        continue
                    if require_schedule_flag and not row.get("is_scheduled"):
                        continue
                    red_texts = [
                        str(value).strip()
                        for value in list(row.get("red_texts") or [])
                        if str(value).strip()
                    ]
                    pending_rows.append({
                        "slot_label": self._format_schedule_slot_label(
                            row.get("thu", "?"),
                            row.get("buoi", "?"),
                            row.get("tiet", "?"),
                        ),
                        "thu": str(row.get("thu", "") or "").strip(),
                        "buoi": str(row.get("buoi", "") or "").strip(),
                        "tiet": str(row.get("tiet", "") or "").strip(),
                        "ngay": str(row.get("ngay", "") or "").strip(),
                        "ppct_hint": str(
                            row.get("ppct_hint")
                            or row.get("tiet_ppct_attr")
                            or row.get("ppct")
                            or ""
                        ).strip(),
                        "mon_hoc_hint": str(
                            row.get("mon_hoc_text_hint")
                            or row.get("mon_hoc_hint")
                            or row.get("mon_hoc")
                            or ""
                        ).strip(),
                        "phan_mon_text_hint": str(
                            row.get("phan_mon_text_hint", "") or ""
                        ).strip(),
                        "noi_dung_hint": str(
                            row.get("noi_dung_hint")
                            or row.get("noi_dung_cong_viec")
                            or ""
                        ).strip(),
                        "red_texts": red_texts,
                    })
                return pending_rows

            def _append_report(tuan_num, lop_text, pending_rows):
                nonlocal pairs_with_pending, total_rows
                if not pending_rows:
                    return
                pairs_with_pending += 1
                total_rows += len(pending_rows)
                reports.append({
                    "week": tuan_num,
                    "week_text": f"Tuần {tuan_num}",
                    "lop": lop_text,
                    "rows": pending_rows,
                })

            def _scan_ui_fallback(tuan_num, lop_text):
                ok_tuan, msg_tuan = _select_dropdown_retry(
                    "tuan",
                    f"Tuần {tuan_num}",
                    attempts=2,
                )
                if not ok_tuan:
                    return False, [], f"Lỗi chọn tuần: {msg_tuan}", False
                ok_week_lops, week_lops_or_error = bridge.get_lop_options()
                if not ok_week_lops:
                    return False, [], (
                        f"Không đọc được danh sách lớp của tuần: {week_lops_or_error}"
                    ), False
                week_lop_map = {
                    str(item or "").strip().casefold(): str(item or "").strip()
                    for item in list(week_lops_or_error or [])
                    if str(item or "").strip()
                }
                actual_lop = week_lop_map.get(lop_text.casefold())
                if not actual_lop:
                    return True, [], "Lớp không xuất hiện ở tuần này", True
                ok_lop, msg_lop = _select_dropdown_retry("lop", actual_lop, attempts=2)
                if not ok_lop:
                    return False, [], f"Lỗi chọn lớp: {msg_lop}", False
                ok_mode, msg_mode = bridge.set_goi_y_khdh_mode(True)
                if not ok_mode:
                    return False, [], f"Không bật được Gợi ý theo KHDH: {msg_mode}", False
                ok_rows, rows_or_error = bridge.read_khdh_suggested_rows()
                if not ok_rows:
                    return False, [], f"Không đọc được row đỏ KHDH: {rows_or_error}", False
                return True, _normalize_pending_rows(rows_or_error, False), "", False

            bridge = ChromeBridge(port=params["port"])
            ok, msg = bridge.connect()
            if not ok:
                q.put(("stats_error", f"Kết nối CDP thất bại: {msg}"))
                return

            ok_ready, ready_msg = bridge.ensure_chi_tiet_sodau_bai(
                params.get("username", ""),
                params.get("password", ""),
            )
            if not ok_ready:
                q.put(("stats_error", ready_msg))
                return

            q.put(("stats_status", "📝 KHBD | Đang lấy metadata lớp thật qua service..."))
            classes_by_week = {}
            ok_discover, discover_payload = bridge.fetch_lop_options_for_weeks_service(
                week_numbers,
                timeout_s=10.0,
                concurrency=6,
            )
            if ok_discover:
                classes_by_week = dict(discover_payload.get("classes_by_week") or {})
                for item in list(discover_payload.get("records") or []):
                    text = str(item.get("text", "") or "").strip()
                    if not text or text.casefold() not in requested_lop_keys:
                        continue
                    current = record_map.setdefault(text.casefold(), {"text": text})
                    for field in ("text", "value", "khoi", "cap", "source", "weeks"):
                        if not current.get(field) and item.get(field):
                            current[field] = copy.deepcopy(item.get(field))
            else:
                q.put((
                    "log",
                    "Service metadata lớp không khả dụng; sẽ dùng fallback UI khi cần: "
                    f"{discover_payload}",
                    "warning",
                ))

            for class_index, lop_text in enumerate(lop_list, start=1):
                class_record = copy.deepcopy(record_map.get(lop_text.casefold()) or {"text": lop_text})
                target_weeks = []
                for week_num in week_numbers:
                    week_classes = classes_by_week.get(str(week_num))
                    if week_classes is not None and lop_text.casefold() not in {
                        str(item or "").strip().casefold() for item in list(week_classes or [])
                    }:
                        skipped_unavailable += 1
                        continue
                    target_weeks.append(week_num)
                if not target_weeks:
                    continue

                q.put((
                    "stats_status",
                    f"📝 KHBD | Service lớp {lop_text} ({class_index}/{len(lop_list)}) | "
                    f"{len(target_weeks)} tuần...",
                ))
                ok_bulk, bulk_payload = bridge.fetch_sodaubai_rows_bulk(
                    lop_text,
                    target_weeks,
                    timeout_s=12.0,
                    concurrency=6,
                    class_meta=class_record,
                    show_goi_y=True,
                )
                bulk_results = {}
                if ok_bulk:
                    bulk_results = {
                        int(item.get("requested_week", 0) or 0): item
                        for item in list(bulk_payload.get("results") or [])
                    }

                for week_num in target_weeks:
                    item = bulk_results.get(week_num) if ok_bulk else None
                    if item and item.get("ok"):
                        pairs_scanned += 1
                        pending_rows = _normalize_pending_rows(
                            list((item.get("payload") or {}).get("rows") or []),
                            True,
                        )
                        _append_report(week_num, lop_text, pending_rows)
                        continue

                    service_error = (
                        str(item.get("error", "") or "")
                        if item
                        else str(bulk_payload)
                    )
                    q.put((
                        "stats_status",
                        f"📝 KHBD | Fallback UI Tuần {week_num} | Lớp {lop_text}...",
                    ))
                    ok_ui, pending_rows, ui_error, unavailable = _scan_ui_fallback(
                        week_num,
                        lop_text,
                    )
                    if _fatal_if_cdp_closed(ui_error):
                        return
                    if unavailable:
                        skipped_unavailable += 1
                        continue
                    if not ok_ui:
                        issues.append({
                            "week": week_num,
                            "lop": lop_text,
                            "message": (
                                f"Service: {service_error or 'không có kết quả'} | "
                                f"Fallback UI: {ui_error}"
                            ),
                        })
                        continue
                    pairs_scanned += 1
                    _append_report(week_num, lop_text, pending_rows)

            q.put(("khdh_pending_done", {
                "tuan_from": int(params["tuan_from"]),
                "tuan_to": int(params["tuan_to"]),
                "lop_count": len(lop_list),
                "pairs_scanned": pairs_scanned,
                "pairs_with_pending": pairs_with_pending,
                "total_rows": total_rows,
                "skipped_unavailable": skipped_unavailable,
                "reports": reports,
                "issues": issues,
            }))
        except Exception as e:
            q.put(("stats_error", f"Worker quét row đỏ KHBD lỗi: {type(e).__name__}: {str(e)[:160]}"))
        finally:
            if bridge:
                try:
                    ok_cleanup, msg_cleanup = bridge.cleanup_after_automation(restore_view_mode=True)
                    q.put((
                        "log",
                        f"Cleanup sau KHDH: {msg_cleanup}" if ok_cleanup else f"Cleanup sau KHDH lỗi: {msg_cleanup}",
                        "info" if ok_cleanup else "warning",
                    ))
                except Exception:
                    pass
                try:
                    bridge.disconnect()
                except Exception:
                    pass

    def _collect_class_stats_report(self, bridge, lop_text, tuan_from, tuan_to,
                                    target_mon_hoc, status_cb=None, class_meta=None):
        """Thu thập report thống kê cho một lớp, dùng chung cho scan 1 lớp và all-class."""
        occurrences_by_ppct = {}
        invalid_ppct_rows = []
        week_errors = []
        latest_occurrence = None
        max_ppct = None
        missing_ppcts = []
        total_rows_with_data = 0
        weeks_scanned = 0
        cache_hits = 0

        for tuan_num in range(tuan_from, tuan_to + 1):
            tuan_text = f"Tuần {tuan_num}"
            if status_cb is not None:
                status_cb(f"⏳ Đang quét {lop_text} — {tuan_text}...")

            ok, payload, meta = self._fetch_class_stats_week_payload(
                bridge,
                lop_text,
                tuan_num,
                class_meta=class_meta,
            )
            weeks_scanned += 1
            if meta.get("from_cache"):
                cache_hits += 1
            if not ok:
                week_errors.append({"week": tuan_num, "message": str(payload)})
                continue

            table_rows = list(payload.get("rows") or [])
            occurrences, invalid_rows, matched_count = self._extract_class_stats_subject_rows(
                table_rows,
                tuan_num,
                target_mon_hoc,
            )
            invalid_ppct_rows.extend(invalid_rows)
            total_rows_with_data += matched_count

            for occurrence in occurrences:
                ppct_value = int(occurrence["ppct"])
                latest_occurrence = occurrence
                max_ppct = ppct_value if max_ppct is None else max(max_ppct, ppct_value)
                occurrences_by_ppct.setdefault(ppct_value, []).append(occurrence)

        duplicate_groups = []
        duplicate_weeks = set()
        unique_ppcts = sorted(occurrences_by_ppct.keys())
        if unique_ppcts:
            start_ppct = unique_ppcts[0]
            end_ppct = unique_ppcts[-1]
            existing_set = set(unique_ppcts)
            missing_ppcts = [
                ppct_value
                for ppct_value in range(start_ppct, end_ppct + 1)
                if ppct_value not in existing_set
            ]

        for ppct_value, items in sorted(occurrences_by_ppct.items()):
            if len(items) <= 1:
                continue
            weeks = sorted({int(item["week"]) for item in items})
            duplicate_weeks.update(weeks)
            duplicate_groups.append({
                "mon_hoc": items[0].get("mon_hoc", "(Không rõ môn)"),
                "ppct": ppct_value,
                "weeks": weeks,
                "items": items,
            })

        max_occurrences = list(occurrences_by_ppct.get(max_ppct, [])) if max_ppct is not None else []
        report = {
            "lop": lop_text,
            "mon_hoc": target_mon_hoc,
            "tuan_from": tuan_from,
            "tuan_to": tuan_to,
            "latest_occurrence": latest_occurrence,
            "max_ppct": max_ppct,
            "max_occurrences": max_occurrences,
            "missing_ppcts": missing_ppcts,
            "total_rows_with_data": total_rows_with_data,
            "duplicate_groups": duplicate_groups,
            "duplicate_weeks": sorted(duplicate_weeks),
            "invalid_ppct_rows": invalid_ppct_rows,
            "week_errors": week_errors,
            "scan_mode": "full",
            "weeks_scanned": weeks_scanned,
            "cache_hits": cache_hits,
        }
        return report

    def _collect_class_stats_fast_max_report(self, bridge, lop_text, tuan_from, tuan_to,
                                             target_mon_hoc, status_cb=None, class_meta=None):
        """Quét nhanh max PPCT bằng cách đi từ tuần cuối về đầu và dừng sớm."""
        week_errors = []
        total_rows_with_data = 0
        weeks_scanned = 0
        cache_hits = 0

        for tuan_num in range(tuan_to, tuan_from - 1, -1):
            tuan_text = f"Tuần {tuan_num}"
            if status_cb is not None:
                status_cb(f"⚡ Đang quét nhanh {lop_text} — {tuan_text}...")

            ok, payload, meta = self._fetch_class_stats_week_payload(
                bridge,
                lop_text,
                tuan_num,
                class_meta=class_meta,
            )
            weeks_scanned += 1
            if meta.get("from_cache"):
                cache_hits += 1
            if not ok:
                week_errors.append({"week": tuan_num, "message": str(payload)})
                continue

            table_rows = list(payload.get("rows") or [])
            occurrences, invalid_rows, matched_count = self._extract_class_stats_subject_rows(
                table_rows,
                tuan_num,
                target_mon_hoc,
            )
            total_rows_with_data += matched_count
            if not occurrences and not invalid_rows:
                continue

            max_ppct = None
            max_occurrences = []
            for occurrence in occurrences:
                ppct_value = int(occurrence["ppct"])
                if max_ppct is None or ppct_value > max_ppct:
                    max_ppct = ppct_value
                    max_occurrences = [occurrence]
                elif ppct_value == max_ppct:
                    max_occurrences.append(occurrence)

            latest_occurrence = max_occurrences[0] if max_occurrences else None
            return {
                "lop": lop_text,
                "mon_hoc": target_mon_hoc,
                "tuan_from": tuan_from,
                "tuan_to": tuan_to,
                "latest_occurrence": latest_occurrence,
                "max_ppct": max_ppct,
                "max_occurrences": max_occurrences,
                "missing_ppcts": [],
                "total_rows_with_data": total_rows_with_data,
                "duplicate_groups": [],
                "duplicate_weeks": [],
                "invalid_ppct_rows": invalid_rows,
                "week_errors": week_errors,
                "scan_mode": "fast_max",
                "weeks_scanned": weeks_scanned,
                "cache_hits": cache_hits,
                "stopped_early": True,
            }

        return {
            "lop": lop_text,
            "mon_hoc": target_mon_hoc,
            "tuan_from": tuan_from,
            "tuan_to": tuan_to,
            "latest_occurrence": None,
            "max_ppct": None,
            "max_occurrences": [],
            "missing_ppcts": [],
            "total_rows_with_data": total_rows_with_data,
            "duplicate_groups": [],
            "duplicate_weeks": [],
            "invalid_ppct_rows": [],
            "week_errors": week_errors,
            "scan_mode": "fast_max",
            "weeks_scanned": weeks_scanned,
            "cache_hits": cache_hits,
            "stopped_early": False,
        }

    def _class_stats_worker(self, params):
        """Worker đọc dữ liệu theo tuần/lớp và phát hiện trùng PPCT."""
        q = self._class_stats_queue
        bridge = None
        try:
            bridge = ChromeBridge(port=params["port"])
            ok, msg = bridge.connect()
            if not ok:
                q.put(("stats_error", f"Kết nối CDP thất bại: {msg}"))
                return

            report = self._collect_class_stats_report(
                bridge,
                lop_text=params["lop"],
                tuan_from=int(params["tuan_from"]),
                tuan_to=int(params["tuan_to"]),
                target_mon_hoc=params["mon_hoc"],
                status_cb=lambda text: q.put(("stats_status", text)),
                class_meta=copy.deepcopy(params.get("class_meta") or {}),
            )
            q.put(("stats_done", report))
        except Exception as e:
            q.put(("stats_error", f"Worker thống kê lỗi: {type(e).__name__}: {str(e)[:140]}"))
        finally:
            if bridge:
                try:
                    bridge.disconnect()
                except Exception:
                    pass

    def _class_stats_fast_overview_task(self, params, lop_text):
        """Task quét nhanh max PPCT cho một lớp, dùng trong overview nhiều lớp."""
        bridge = None
        class_meta = next((
            copy.deepcopy(item)
            for item in list(params.get("class_records") or [])
            if str(item.get("text", "") or "").strip().casefold()
            == str(lop_text).strip().casefold()
        ), {})
        try:
            bridge = ChromeBridge(port=params["port"])
            ok, msg = bridge.connect()
            if not ok:
                return {
                    "lop": lop_text,
                    "mon_hoc": params["mon_hoc"],
                    "tuan_from": int(params["tuan_from"]),
                    "tuan_to": int(params["tuan_to"]),
                    "latest_occurrence": None,
                    "max_ppct": None,
                    "max_occurrences": [],
                    "missing_ppcts": [],
                    "total_rows_with_data": 0,
                    "duplicate_groups": [],
                    "duplicate_weeks": [],
                    "invalid_ppct_rows": [],
                    "week_errors": [{
                        "week": int(params["tuan_to"]),
                        "message": f"Kết nối CDP thất bại: {msg}",
                    }],
                    "scan_mode": "fast_max",
                    "weeks_scanned": 0,
                    "cache_hits": 0,
                    "stopped_early": False,
                }

            return self._collect_class_stats_fast_max_report(
                bridge,
                lop_text=lop_text,
                tuan_from=int(params["tuan_from"]),
                tuan_to=int(params["tuan_to"]),
                target_mon_hoc=params["mon_hoc"],
                status_cb=None,
                class_meta=class_meta,
            )
        except Exception as e:
            return {
                "lop": lop_text,
                "mon_hoc": params["mon_hoc"],
                "tuan_from": int(params["tuan_from"]),
                "tuan_to": int(params["tuan_to"]),
                "latest_occurrence": None,
                "max_ppct": None,
                "max_occurrences": [],
                "missing_ppcts": [],
                "total_rows_with_data": 0,
                "duplicate_groups": [],
                "duplicate_weeks": [],
                "invalid_ppct_rows": [],
                "week_errors": [{
                    "week": int(params["tuan_to"]),
                    "message": f"Worker overview lỗi: {type(e).__name__}: {str(e)[:140]}",
                }],
                "scan_mode": "fast_max",
                "weeks_scanned": 0,
                "cache_hits": 0,
                "stopped_early": False,
            }
        finally:
            if bridge:
                try:
                    bridge.disconnect()
                except Exception:
                    pass

    def _class_stats_full_overview_task(self, params, lop_text):
        """Task quét FULL cho một lớp để phát hiện thiếu/trùng PPCT."""
        q = self._class_stats_queue
        bridge = None
        class_meta = next((
            copy.deepcopy(item)
            for item in list(params.get("class_records") or [])
            if str(item.get("text", "") or "").strip().casefold()
            == str(lop_text).strip().casefold()
        ), {})
        try:
            bridge = ChromeBridge(port=params["port"])
            ok, msg = bridge.connect()
            if not ok:
                return {
                    "lop": lop_text,
                    "mon_hoc": params["mon_hoc"],
                    "tuan_from": int(params["tuan_from"]),
                    "tuan_to": int(params["tuan_to"]),
                    "latest_occurrence": None,
                    "max_ppct": None,
                    "max_occurrences": [],
                    "missing_ppcts": [],
                    "total_rows_with_data": 0,
                    "duplicate_groups": [],
                    "duplicate_weeks": [],
                    "invalid_ppct_rows": [],
                    "week_errors": [{
                        "week": int(params["tuan_to"]),
                        "message": f"Kết nối CDP thất bại: {msg}",
                    }],
                    "scan_mode": "full",
                    "weeks_scanned": 0,
                    "cache_hits": 0,
                }

            return self._collect_class_stats_report(
                bridge,
                lop_text=lop_text,
                tuan_from=int(params["tuan_from"]),
                tuan_to=int(params["tuan_to"]),
                target_mon_hoc=params["mon_hoc"],
                status_cb=None,
                class_meta=class_meta,
            )
        except Exception as e:
            return {
                "lop": lop_text,
                "mon_hoc": params["mon_hoc"],
                "tuan_from": int(params["tuan_from"]),
                "tuan_to": int(params["tuan_to"]),
                "latest_occurrence": None,
                "max_ppct": None,
                "max_occurrences": [],
                "missing_ppcts": [],
                "total_rows_with_data": 0,
                "duplicate_groups": [],
                "duplicate_weeks": [],
                "invalid_ppct_rows": [],
                "week_errors": [{
                    "week": int(params["tuan_to"]),
                    "message": f"Worker full overview lỗi: {type(e).__name__}: {str(e)[:140]}",
                }],
                "scan_mode": "full",
                "weeks_scanned": 0,
                "cache_hits": 0,
            }
        finally:
            if bridge:
                try:
                    ok_cleanup, msg_cleanup = bridge.cleanup_after_automation(restore_view_mode=False)
                    q.put(("stats_status", f"Đã cleanup trạng thái web sau thống kê: {msg_cleanup}" if ok_cleanup else msg_cleanup))
                except Exception:
                    pass
                try:
                    bridge.disconnect()
                except Exception:
                    pass

    def _class_stats_all_worker(self, params):
        """Worker tổng hợp nhanh PPCT cao nhất cho toàn bộ lớp."""
        q = self._class_stats_queue
        reports = []
        try:
            lop_list = list(params.get("lop_list") or [])
            total = len(lop_list)
            scan_mode = str(params.get("scan_mode", "fast_max") or "fast_max")
            full_scan = scan_mode == "full_missing"
            if not lop_list:
                q.put(("stats_error", "Không có lớp nào để tổng hợp."))
                return

            max_workers = min(2 if full_scan else 3, max(1, total))
            mode_label = "FULL thiếu PPCT" if full_scan else "quét nhanh"
            q.put((
                "stats_status",
                f"{'🔎' if full_scan else '⚡'} Đang {mode_label} {total} lớp | "
                f"Môn {params['mon_hoc']} | {max_workers} luồng...",
            ))
            completed = 0
            task = self._class_stats_full_overview_task if full_scan else self._class_stats_fast_overview_task
            with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="classstats") as executor:
                future_map = {
                    executor.submit(task, params, lop_text): lop_text
                    for lop_text in lop_list
                }
                for future in as_completed(future_map):
                    lop_text = future_map[future]
                    report = future.result()
                    reports.append(report)
                    completed += 1
                    max_ppct = report.get("max_ppct")
                    max_text = str(max_ppct) if max_ppct is not None else "--"
                    missing_count = len(report.get("missing_ppcts") or [])
                    q.put((
                        "stats_status",
                        f"{'🔎' if full_scan else '⚡'} Đã xong {completed}/{total}: "
                        f"{lop_text} | Max PPCT {max_text}"
                        + (f" | Thiếu {missing_count}" if full_scan else ""),
                    ))

            q.put(("stats_overview_done", {
                "mon_hoc": params["mon_hoc"],
                "tuan_from": int(params["tuan_from"]),
                "tuan_to": int(params["tuan_to"]),
                "reports": reports,
                "scan_mode": "full" if full_scan else "fast_max",
                "max_workers": max_workers,
                "weeks_scanned": sum(int(r.get("weeks_scanned", 0) or 0) for r in reports),
                "cache_hits": sum(int(r.get("cache_hits", 0) or 0) for r in reports),
            }))
        except Exception as e:
            q.put(("stats_error", f"Worker tổng hợp lớp lỗi: {type(e).__name__}: {str(e)[:140]}"))

    def _render_class_stats_report(self, report):
        """Render kết quả thống kê lớp vào text area."""
        if not self._class_stats_result_text or not self._class_stats_result_text.winfo_exists():
            return

        txt = self._class_stats_result_text
        txt.config(state="normal")
        txt.delete("1.0", "end")

        lop_text = report.get("lop", "?")
        mon_hoc_text = report.get("mon_hoc", "?")
        tuan_from = report.get("tuan_from", "?")
        tuan_to = report.get("tuan_to", "?")
        latest_occurrence = report.get("latest_occurrence")
        max_ppct = report.get("max_ppct")
        max_occurrences = report.get("max_occurrences", [])
        missing_ppcts = report.get("missing_ppcts", [])
        duplicate_groups = report.get("duplicate_groups", [])
        duplicate_weeks = report.get("duplicate_weeks", [])
        invalid_ppct_rows = report.get("invalid_ppct_rows", [])
        week_errors = report.get("week_errors", [])
        aliased_weeks = [
            item for item in week_errors
            if "Bảng trả về đúng dữ liệu của Tuần" in str(item.get("message", ""))
        ]
        real_week_errors = [
            item for item in week_errors
            if item not in aliased_weeks
        ]
        total_rows = report.get("total_rows_with_data", 0)

        txt.insert("end", f"Thống kê lớp {lop_text} | Môn {mon_hoc_text}\n", "title")
        txt.insert("end", f"Phạm vi quét: Tuần {tuan_from} → Tuần {tuan_to}\n")
        txt.insert("end", f"Tổng số tiết đã có dữ liệu: {total_rows}\n")

        if latest_occurrence:
            txt.insert(
                "end",
                "PPCT gần nhất: "
                f"{latest_occurrence.get('ppct')} | {latest_occurrence.get('week_text')} | "
                f"{latest_occurrence.get('slot_label')} | {latest_occurrence.get('mon_hoc', '')}\n",
                "ok",
            )
        else:
            txt.insert("end", "PPCT gần nhất: chưa tìm thấy dữ liệu hợp lệ cho môn đã chọn\n", "warn")

        if max_ppct is not None:
            txt.insert("end", f"PPCT cao nhất đã thấy: {max_ppct}\n", "ok")
            if max_occurrences:
                first_max = max_occurrences[0]
                txt.insert(
                    "end",
                    f"PPCT cao nhất xuất hiện tại: {first_max.get('week_text')} | "
                    f"{first_max.get('slot_label')}\n",
                    "ok",
                )
        else:
            txt.insert("end", "PPCT cao nhất đã thấy: chưa có\n", "warn")

        if missing_ppcts:
            display_list = ", ".join(str(x) for x in missing_ppcts[:30])
            if len(missing_ppcts) > 30:
                display_list += f"... (+{len(missing_ppcts) - 30})"
            txt.insert("end", f"PPCT bị thiếu trong dải hiện có: {display_list}\n", "warn")
        else:
            txt.insert("end", "PPCT bị thiếu trong dải hiện có: không phát hiện\n", "ok")

        txt.insert("end", "\n")

        if duplicate_groups:
            txt.insert(
                "end",
                "CẢNH BÁO TRÙNG TIẾT PPCT CÙNG MÔN\n",
                "error",
            )
            txt.insert(
                "end",
                "Các tuần bị trùng: " + ", ".join(str(x) for x in duplicate_weeks) + "\n",
                "error",
            )
            for group in duplicate_groups:
                txt.insert(
                    "end",
                    f"- {group.get('mon_hoc', '(Không rõ môn)')} | PPCT {group['ppct']} "
                    f"trùng ở tuần {', '.join(str(x) for x in group['weeks'])}\n",
                    "error",
                )
                for item in group["items"]:
                    txt.insert(
                        "end",
                        f"    {item['week_text']} | {item['slot_label']} | {item.get('mon_hoc', '')}\n",
                        "error",
                    )
        else:
            txt.insert("end", "Không phát hiện PPCT trùng trong khoảng tuần đã quét.\n", "ok")

        txt.insert("end", "\n")

        if invalid_ppct_rows:
            txt.insert("end", "Tiết có dữ liệu nhưng PPCT bất thường\n", "warn")
            for item in invalid_ppct_rows:
                txt.insert(
                    "end",
                    f"- {item['week_text']} | {item['slot_label']} | {item.get('mon_hoc', '')} | {item['message']}\n",
                    "warn",
                )
            txt.insert("end", "\n")

        if aliased_weeks:
            txt.insert("end", "Tuần trả dữ liệu cũ / alias\n", "warn")
            for item in aliased_weeks:
                txt.insert(
                    "end",
                    f"- Tuần {item['week']}: {item['message']}\n",
                    "warn",
                )
            txt.insert("end", "\n")

        if real_week_errors:
            txt.insert("end", "Tuần quét lỗi / không đọc được\n", "error")
            for item in real_week_errors:
                txt.insert(
                    "end",
                    f"- Tuần {item['week']}: {item['message']}\n",
                    "error",
                )
            txt.insert("end", "\n")

        txt.config(state="disabled")

    def _render_class_stats_overview(self, payload):
        """Render bảng tổng hợp PPCT cao nhất cho toàn bộ lớp."""
        if not self._class_stats_result_text or not self._class_stats_result_text.winfo_exists():
            return

        txt = self._class_stats_result_text
        txt.config(state="normal")
        txt.delete("1.0", "end")

        mon_hoc_text = payload.get("mon_hoc", "?")
        tuan_from = payload.get("tuan_from", "?")
        tuan_to = payload.get("tuan_to", "?")
        reports = list(payload.get("reports") or [])
        scan_mode = str(payload.get("scan_mode", "fast_max") or "fast_max").strip()
        weeks_scanned = int(payload.get("weeks_scanned", 0) or 0)
        cache_hits = int(payload.get("cache_hits", 0) or 0)
        max_workers = int(payload.get("max_workers", 1) or 1)

        txt.insert("end", f"Tổng hợp PPCT cao nhất các lớp | Môn {mon_hoc_text}\n", "title")
        txt.insert("end", f"Phạm vi quét: Tuần {tuan_from} → Tuần {tuan_to}\n")
        txt.insert(
            "end",
            f"Số lớp đã tổng hợp: {len(reports)} | Chế độ: {scan_mode} | "
            f"Tuần đã quét thực tế: {weeks_scanned} | Cache hit: {cache_hits} | "
            f"Luồng: {max_workers}\n\n",
        )
        txt.insert(
            "end",
            "Lớp   | Max PPCT | Tuần      | Slot cao nhất         | Mode | Alias | Trùng | Thiếu\n",
            "title",
        )
        txt.insert("end", "-" * 96 + "\n", "muted")

        for report in sorted(reports, key=lambda item: str(item.get("lop", ""))):
            lop_text = str(report.get("lop", "") or "?")
            max_ppct = report.get("max_ppct")
            max_occurrences = list(report.get("max_occurrences") or [])
            missing_ppcts = list(report.get("missing_ppcts") or [])
            duplicate_groups = list(report.get("duplicate_groups") or [])
            week_errors = list(report.get("week_errors") or [])
            report_mode = str(report.get("scan_mode", "fast_max") or "fast_max").strip()
            alias_count = sum(
                1
                for item in week_errors
                if "Bảng trả về đúng dữ liệu của Tuần" in str(item.get("message", ""))
            )

            if max_occurrences:
                top_item = max_occurrences[0]
                week_text = str(top_item.get("week_text", "--"))
                slot_text = str(top_item.get("slot_label", "--"))
            else:
                week_text = "--"
                slot_text = "--"

            max_text = str(max_ppct) if max_ppct is not None else "--"
            duplicate_text = str(len(duplicate_groups)) if report_mode == "full" else "--"
            missing_text = str(len(missing_ppcts)) if report_mode == "full" else "--"
            mode_text = "FAST" if report_mode == "fast_max" else "FULL"
            line = (
                f"{lop_text:<5} | {max_text:>8} | {week_text:<9} | "
                f"{slot_text:<20} | {mode_text:<4} | {alias_count:>5} | {duplicate_text:>5} | "
                f"{missing_text:>5}\n"
            )
            tag = "ok"
            if max_ppct is None:
                tag = "muted"
            elif alias_count or (report_mode == "full" and (duplicate_groups or missing_ppcts)):
                tag = "warn"
            txt.insert("end", line, tag)

        txt.insert("end", "\n")
        txt.insert("end", "Cột 'Alias' = số tuần trả lại đúng dữ liệu của tuần khác.\n", "muted")
        if scan_mode == "full":
            txt.insert("end", "\nChi tiết lớp thiếu/trùng PPCT\n", "title")
            any_detail = False
            for report in sorted(reports, key=lambda item: str(item.get("lop", ""))):
                lop_text = str(report.get("lop", "") or "?")
                missing_ppcts = list(report.get("missing_ppcts") or [])
                duplicate_groups = list(report.get("duplicate_groups") or [])
                invalid_ppct_rows = list(report.get("invalid_ppct_rows") or [])
                if not (missing_ppcts or duplicate_groups or invalid_ppct_rows):
                    continue
                any_detail = True
                txt.insert("end", f"- {lop_text}:\n", "warn")
                if missing_ppcts:
                    display_list = ", ".join(str(x) for x in missing_ppcts[:60])
                    if len(missing_ppcts) > 60:
                        display_list += f"... (+{len(missing_ppcts) - 60})"
                    txt.insert("end", f"    Thiếu PPCT: {display_list}\n", "warn")
                if duplicate_groups:
                    duplicate_text = ", ".join(
                        f"{group.get('ppct')} (tuần {', '.join(str(x) for x in group.get('weeks', []))})"
                        for group in duplicate_groups[:20]
                    )
                    if len(duplicate_groups) > 20:
                        duplicate_text += f"... (+{len(duplicate_groups) - 20})"
                    txt.insert("end", f"    Trùng PPCT: {duplicate_text}\n", "error")
                if invalid_ppct_rows:
                    txt.insert(
                        "end",
                        f"    PPCT bất thường: {len(invalid_ppct_rows)} tiết có dữ liệu nhưng PPCT trống/không hợp lệ\n",
                        "warn",
                    )
            if not any_detail:
                txt.insert("end", "Không phát hiện lớp thiếu/trùng PPCT trong khoảng tuần đã quét.\n", "ok")
        else:
            txt.insert("end", "Mode FAST = quét từ tuần cuối về đầu và dừng ở tuần gần nhất có dữ liệu môn.\n", "muted")
            txt.insert("end", "Ở Mode FAST, cột 'Trùng' và 'Thiếu' hiển thị '--' vì chưa quét đầy đủ.\n", "muted")
        txt.config(state="disabled")

    def _render_missing_teacher_audit(self, payload):
        """Render kết quả quét GV chưa nhập từ màn thống kê."""
        if not self._class_stats_result_text or not self._class_stats_result_text.winfo_exists():
            return

        txt = self._class_stats_result_text
        txt.config(state="normal")
        txt.delete("1.0", "end")

        tuan_from = int(payload.get("tuan_from", 0) or 0)
        tuan_to = int(payload.get("tuan_to", 0) or 0)
        reports = list(payload.get("reports") or [])
        issues = list(payload.get("issues") or [])
        pairs_scanned = int(payload.get("pairs_scanned", 0) or 0)
        pairs_with_missing = int(payload.get("pairs_with_missing", 0) or 0)
        class_count = int(payload.get("class_count", 0) or 0)
        teacher_rows = sum(len(item.get("teachers") or []) for item in reports)

        txt.insert("end", "GV chưa nhập theo màn thống kê VnEdu\n", "title")
        txt.insert("end", f"Phạm vi quét: Tuần {tuan_from} → Tuần {tuan_to}\n")
        txt.insert(
            "end",
            f"Lớp đã quét: {class_count} | Cặp tuần/lớp đã đọc: {pairs_scanned} | "
            f"Cặp có thiếu: {pairs_with_missing} | Dòng giáo viên thiếu: {teacher_rows}\n\n",
        )

        if not reports:
            txt.insert("end", "Không phát hiện giáo viên nào chưa nhập trong phạm vi đã quét.\n", "ok")
        else:
            for report in reports:
                txt.insert(
                    "end",
                    f"{report.get('week_text', 'Tuần ?')} | Lớp {report.get('lop', '?')}\n",
                    "section",
                )
                for teacher in list(report.get("teachers") or []):
                    teacher_name = str(teacher.get("teacher_name", "") or "").strip() or "(Không rõ giáo viên)"
                    subject_text = str(teacher.get("mon_hoc", "") or "").strip() or "(Không rõ môn)"
                    total_missing = int(teacher.get("total_missing", 0) or 0)
                    txt.insert(
                        "end",
                        f"- {teacher_name} | {subject_text} | Tổng thiếu {total_missing}\n",
                        "title",
                    )

                    exact_slots = list(teacher.get("exact_slots") or [])
                    ambiguous_buckets = list(teacher.get("ambiguous_buckets") or [])
                    unmatched_buckets = list(teacher.get("unmatched_buckets") or [])

                    if exact_slots:
                        for slot in exact_slots:
                            txt.insert(
                                "end",
                                f"    ✓ {slot.get('slot_label', '--')}\n",
                                "ok",
                            )
                    if ambiguous_buckets:
                        for bucket in ambiguous_buckets:
                            candidates = ", ".join(
                                slot.get("slot_label", "--")
                                for slot in list(bucket.get("candidate_slots") or [])
                            ) or "không có"
                            txt.insert(
                                "end",
                                f"    ? {bucket.get('buoi', '?')} Thứ {bucket.get('thu', '?')} | "
                                f"Thiếu {bucket.get('count', '?')} | Ứng viên: {candidates}\n",
                                "warn",
                            )
                    if unmatched_buckets:
                        for bucket in unmatched_buckets:
                            txt.insert(
                                "end",
                                f"    ! {bucket.get('buoi', '?')} Thứ {bucket.get('thu', '?')} | "
                                f"Thiếu {bucket.get('count', '?')} | {bucket.get('note', '')}\n",
                                "error",
                            )
                    if not (exact_slots or ambiguous_buckets or unmatched_buckets):
                        txt.insert(
                            "end",
                            "    Không suy ra được bucket/tiết cụ thể từ dữ liệu hiện có.\n",
                            "warn",
                        )
                txt.insert("end", "\n")

        if issues:
            txt.insert("end", "Các cặp tuần/lớp đọc lỗi hoặc thiếu dữ liệu\n", "section")
            for item in issues[:120]:
                lop_text = str(item.get("lop", "") or "").strip()
                prefix = f"Tuần {item.get('week', '?')}"
                if lop_text:
                    prefix += f" | {lop_text}"
                txt.insert("end", f"- {prefix}: {item.get('message', '')}\n", "error")
            if len(issues) > 120:
                txt.insert("end", f"... còn {len(issues) - 120} lỗi khác\n", "muted")

        txt.config(state="disabled")

    def _render_khdh_pending_audit(self, payload):
        """Render kết quả quét row đỏ KHBD/KHDH chưa lên."""
        if not self._class_stats_result_text or not self._class_stats_result_text.winfo_exists():
            return

        txt = self._class_stats_result_text
        txt.config(state="normal")
        txt.delete("1.0", "end")

        tuan_from = int(payload.get("tuan_from", 0) or 0)
        tuan_to = int(payload.get("tuan_to", 0) or 0)
        lop_count = int(payload.get("lop_count", 0) or 0)
        pairs_scanned = int(payload.get("pairs_scanned", 0) or 0)
        pairs_with_pending = int(payload.get("pairs_with_pending", 0) or 0)
        total_rows = int(payload.get("total_rows", 0) or 0)
        skipped_unavailable = int(payload.get("skipped_unavailable", 0) or 0)
        reports = list(payload.get("reports") or [])
        issues = list(payload.get("issues") or [])

        txt.insert("end", "Row đỏ KHBD/KHDH chưa lên lịch\n", "title")
        txt.insert("end", f"Phạm vi quét: Tuần {tuan_from} → Tuần {tuan_to}\n")
        txt.insert(
            "end",
            f"Lớp đã quét: {lop_count} | Cặp tuần/lớp đã đọc: {pairs_scanned} | "
            f"Cặp còn row đỏ: {pairs_with_pending} | Tổng row đỏ còn lại: {total_rows}\n\n",
        )
        if skipped_unavailable:
            txt.insert(
                "end",
                f"Đã bỏ qua {skipped_unavailable} cặp tuần/lớp không tồn tại trong tuần tương ứng.\n\n",
                "muted",
            )

        if not reports:
            txt.insert("end", "Không phát hiện row đỏ KHBD/KHDH nào còn nút + trong phạm vi đã quét.\n", "ok")
        else:
            for report in reports:
                txt.insert(
                    "end",
                    f"{report.get('week_text', 'Tuần ?')} | Lớp {report.get('lop', '?')} | "
                    f"{len(report.get('rows') or [])} row đỏ\n",
                    "section",
                )
                for item in list(report.get("rows") or []):
                    mon_hoc = str(item.get("mon_hoc_hint", "") or "").strip() or "(Không rõ môn)"
                    ppct_hint = str(item.get("ppct_hint", "") or "").strip() or "--"
                    noi_dung_hint = str(item.get("noi_dung_hint", "") or "").strip()
                    line = (
                        f"- {item.get('slot_label', '--')} | {mon_hoc} | PPCT {ppct_hint}"
                    )
                    if noi_dung_hint:
                        line += f" | {noi_dung_hint}"
                    txt.insert("end", line + "\n", "warn")
                txt.insert("end", "\n")

        if issues:
            txt.insert("end", "Các cặp tuần/lớp đọc lỗi\n", "section")
            for item in issues[:160]:
                txt.insert(
                    "end",
                    f"- Tuần {item.get('week', '?')} | {item.get('lop', '?')}: {item.get('message', '')}\n",
                    "error",
                )
            if len(issues) > 160:
                txt.insert("end", f"... còn {len(issues) - 160} lỗi khác\n", "muted")

        txt.config(state="disabled")

    def _poll_class_stats_queue(self):
        """Poll queue cho dialog thống kê lớp."""
        try:
            while not self._class_stats_queue.empty():
                msg = self._class_stats_queue.get_nowait()
                msg_type = msg[0]

                if msg_type == "options_ready":
                    self._class_stats_thread = None
                    payload = msg[1]
                    lop_options = [x for x in payload.get("lop_options", []) if x and not x.startswith("--")]
                    self._class_stats_lop_records = list(payload.get("lop_records", []) or [])
                    tuan_options = payload.get("tuan_options", [])
                    mon_hoc_options = payload.get("mon_hoc_options", [])
                    mon_hoc_note = str(payload.get("mon_hoc_note", "") or "").strip()
                    class_scan_scope = str(payload.get("class_scan_scope", "") or "").strip()
                    class_weeks_scanned = int(payload.get("class_weeks_scanned", 0) or 0)
                    self._class_stats_tuan_options = list(tuan_options)
                    self._populate_class_stats_buttons(lop_options)
                    selected_subject = self._populate_class_stats_mon_hoc(mon_hoc_options)

                    nums = []
                    for item in tuan_options:
                        match = re.search(r"\d+", str(item))
                        if match:
                            nums.append(int(match.group()))
                    if nums:
                        self.var_stats_tuan_from.set(min(nums))
                        self.var_stats_tuan_to.set(max(nums))
                    scope_note = (
                        f" DS lớp quét {class_weeks_scanned} tuần ({class_scan_scope})."
                        if class_scan_scope else ""
                    )
                    self._set_class_stats_status(
                        f"Đã tải {len(lop_options)} lớp, {len(mon_hoc_options)} môn.{scope_note} "
                        f"Chọn môn '{selected_subject or '(chưa chọn)'}' để quét PPCT, "
                        f"hoặc dùng nút 'GV chưa nhập' để rà giáo viên thiếu theo tuần/lớp.",
                        "#1f7a1f",
                    )
                    self._log(
                        f"Đã tải {len(lop_options)} lớp, {len(mon_hoc_options)} môn cho thống kê",
                        "success",
                    )
                    if mon_hoc_note:
                        self._log(mon_hoc_note, "warning")

                elif msg_type == "options_error":
                    self._class_stats_thread = None
                    self._set_class_stats_status(msg[1], "#b00020")
                    self._log(msg[1], "error")

                elif msg_type == "stats_status":
                    self._set_class_stats_status(msg[1], "#a86400")

                elif msg_type == "log":
                    self._log(msg[1], msg[2] if len(msg) > 2 else "info")

                elif msg_type == "stats_done":
                    self._class_stats_thread = None
                    report = msg[1]
                    self._render_class_stats_report(report)
                    duplicate_groups = report.get("duplicate_groups", [])
                    duplicate_weeks = report.get("duplicate_weeks", [])
                    mon_hoc_text = report.get("mon_hoc", "?")
                    if duplicate_groups:
                        self._set_class_stats_status(
                            f"Lớp {report['lop']} | Môn {mon_hoc_text} có PPCT trùng ở tuần: "
                            + ", ".join(str(x) for x in duplicate_weeks),
                            "#b00020",
                        )
                        self._log(
                            f"⚠ Thống kê lớp {report['lop']} | Môn {mon_hoc_text}: phát hiện {len(duplicate_groups)} nhóm PPCT trùng",
                            "warning",
                        )
                    else:
                        self._set_class_stats_status(
                            f"Lớp {report['lop']} | Môn {mon_hoc_text}: không phát hiện PPCT trùng trong phạm vi quét.",
                            "#1f7a1f",
                        )
                        self._log(
                            f"📊 Thống kê lớp {report['lop']} | Môn {mon_hoc_text} hoàn tất: không có PPCT trùng",
                            "success",
                        )
                    self._set_class_stats_running(False)
                    return

                elif msg_type == "stats_overview_done":
                    self._class_stats_thread = None
                    payload = msg[1]
                    reports = list(payload.get("reports") or [])
                    self._render_class_stats_overview(payload)
                    weeks_scanned = int(payload.get("weeks_scanned", 0) or 0)
                    cache_hits = int(payload.get("cache_hits", 0) or 0)
                    self._set_class_stats_status(
                        f"Đã quét nhanh {len(reports)} lớp cho môn {payload.get('mon_hoc', '?')} "
                        f"| Tuần thực quét: {weeks_scanned} | Cache hit: {cache_hits}.",
                        "#1f7a1f",
                    )
                    self._log(
                        f"⚡ Đã tổng hợp nhanh PPCT cao nhất cho {len(reports)} lớp | "
                        f"Môn {payload.get('mon_hoc', '?')} | Tuần thực quét {weeks_scanned} | "
                        f"Cache hit {cache_hits}",
                        "success",
                    )
                    self._set_class_stats_running(False)
                    return

                elif msg_type == "missing_teacher_done":
                    self._class_stats_thread = None
                    payload = msg[1]
                    reports = list(payload.get("reports") or [])
                    pairs_with_missing = int(payload.get("pairs_with_missing", 0) or 0)
                    issues = list(payload.get("issues") or [])
                    self._render_missing_teacher_audit(payload)
                    self._set_class_stats_status(
                        f"Đã quét GV chưa nhập: {len(reports)} cặp tuần/lớp có thiếu | "
                        f"Lỗi đọc: {len(issues)} | Cặp có thiếu: {pairs_with_missing}.",
                        "#1f7a1f" if not issues else "#a86400",
                    )
                    self._log(
                        f"👤 Hoàn tất quét GV chưa nhập | Có thiếu: {len(reports)} cặp tuần/lớp | "
                        f"Lỗi: {len(issues)}",
                        "success" if not issues else "warning",
                    )
                    self._set_class_stats_running(False)
                    return

                elif msg_type == "khdh_pending_done":
                    self._class_stats_thread = None
                    payload = msg[1]
                    reports = list(payload.get("reports") or [])
                    issues = list(payload.get("issues") or [])
                    total_rows = int(payload.get("total_rows", 0) or 0)
                    skipped_unavailable = int(payload.get("skipped_unavailable", 0) or 0)
                    self._render_khdh_pending_audit(payload)
                    self._set_class_stats_status(
                        f"Đã quét row đỏ KHBD/KHDH: {len(reports)} cặp tuần/lớp còn việc | "
                        f"Tổng row đỏ: {total_rows} | Bỏ qua không có lớp: {skipped_unavailable} | Lỗi đọc: {len(issues)}.",
                        "#1f7a1f" if not issues else "#a86400",
                    )
                    self._log(
                        f"📝 Hoàn tất quét row đỏ KHBD/KHDH | Cặp còn việc: {len(reports)} | "
                        f"Row đỏ: {total_rows} | Bỏ qua không có lớp: {skipped_unavailable} | Lỗi: {len(issues)}",
                        "success" if not issues else "warning",
                    )
                    self._set_class_stats_running(False)
                    return

                elif msg_type == "stats_error":
                    self._class_stats_thread = None
                    self._set_class_stats_status(msg[1], "#b00020")
                    self._log(msg[1], "error")
                    self._set_class_stats_running(False)
                    return

        except Exception as e:
            self._set_class_stats_status(f"Lỗi poll thống kê: {e}", "#b00020")
            self._class_stats_thread = None
            self._set_class_stats_running(False)
            return

        if (
            (self._class_stats_running or (self._class_stats_thread and self._class_stats_thread.is_alive()))
            and self._root_exists()
        ):
            self.root.after(150, self._poll_class_stats_queue)

    # ----- Buttons -----

    def _build_buttons(self, parent):
        """Tạo toolbar nhỏ cho các thao tác UI chung."""
        self.btn_compact = ttk.Button(
            parent, text="Thu gọn", command=self._toggle_compact,
            style="Subtle.TButton",
        )
        self.btn_compact.pack(side="right", padx=1)

    def _build_class_stats_toolbar(self, parent):
        """Nút mở thống kê PPCT/lớp nằm dưới log."""
        self.btn_open_class_stats = ttk.Button(
            parent,
            text="Quét lớp và thống kê PPCT",
            style="Highlight.TButton",
            command=self._on_open_class_stats_dialog,
        )
        self.btn_open_class_stats.pack(anchor="w")

    # ----- Log -----

    def _build_log(self, parent):
        """Tạo Text widget cho log."""
        self.txt_log = tk.Text(
            parent, height=6, font=("Consolas", 10),
            state="disabled", wrap="word",
            bg=UI_LOG_BG, fg=UI_LOG_TEXT,
            insertbackground="#ffffff"
        )
        scrollbar = ttk.Scrollbar(parent, orient="vertical",
                                  command=self.txt_log.yview)
        self.txt_log.config(yscrollcommand=scrollbar.set)
        self.txt_log.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def _log(self, message, level="info"):
        """Ghi log message.

        Args:
            message: Nội dung log
            level: "info" | "success" | "warning" | "error"
        """
        timestamp = datetime.now().strftime("%H:%M:%S")
        prefix_map = {
            "info": "ℹ️",
            "success": "✅",
            "warning": "⚠️",
            "error": "❌",
        }
        prefix = prefix_map.get(level, "")
        line = f"{timestamp}  {prefix} {message}\n"

        self.txt_log.config(state="normal")
        self.txt_log.insert("end", line)
        self.txt_log.see("end")
        self.txt_log.config(state="disabled")

        # Giới hạn 500 dòng
        line_count = int(self.txt_log.index("end-1c").split(".")[0])
        if line_count > 500:
            self.txt_log.config(state="normal")
            self.txt_log.delete("1.0", "100.0")
            self.txt_log.config(state="disabled")

    # -----------------------------------------------------------------
    # CDP CONNECTION LOGIC
    # -----------------------------------------------------------------

    def _find_chrome_exe(self):
        """Tìm đường dẫn chrome.exe trên hệ thống.

        Returns:
            str hoặc None: Đường dẫn đầy đủ tới chrome.exe nếu tìm thấy.
        """
        import os
        candidates = [
            os.path.join(os.environ.get("ProgramFiles", ""), "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
        ]
        for path in candidates:
            if path and os.path.isfile(path):
                return path
        return None

    def _get_cdp_port(self):
        """Trả về CDP port cố định của app."""
        self.var_cdp_port.set(DEFAULT_CDP_PORT)
        return DEFAULT_CDP_PORT

    def _list_debug_profile_chrome_pids(self):
        """Liệt kê PID chrome.exe đang dùng user-data-dir debug riêng của app."""
        import subprocess
        import json
        import os

        profile_dir = os.path.normcase(os.path.normpath(CHROME_DEBUG_PROFILE_DIR)).replace("/", "\\")
        ps_script = (
            "$procs = Get-CimInstance Win32_Process -Filter \"name = 'chrome.exe'\" | "
            "Select-Object ProcessId, CommandLine; "
            "$procs | ConvertTo-Json -Compress"
        )
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if proc.returncode != 0:
                return []
            raw = (proc.stdout or "").strip()
            if not raw:
                return []
            data = json.loads(raw)
            if isinstance(data, dict):
                data = [data]
            pids = []
            for item in data:
                cmd = str(item.get("CommandLine") or "")
                if not cmd:
                    continue
                cmd_norm = os.path.normcase(cmd).replace("/", "\\")
                if profile_dir in cmd_norm:
                    try:
                        pid = int(item.get("ProcessId") or 0)
                    except (TypeError, ValueError):
                        pid = 0
                    if pid > 0:
                        pids.append(pid)
            return sorted(set(pids))
        except Exception:
            return []

    def _terminate_debug_profile_chrome(self, pids):
        """Chỉ đóng các process Chrome debug riêng của app, không ảnh hưởng Chrome thường."""
        import subprocess
        import time

        terminated = 0
        for pid in list(pids or []):
            try:
                proc = subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if int(getattr(proc, "returncode", 1)) == 0:
                    terminated += 1
                else:
                    stderr_text = str(getattr(proc, "stderr", "") or "").strip()
                    if stderr_text:
                        self._log(
                            f"Không thể đóng Chrome PID {pid}: {stderr_text[:180]}",
                            "warning",
                        )
            except Exception:
                continue
        if terminated:
            time.sleep(0.8)
        return terminated

    def _launch_or_reuse_chrome_auto_sync(self):
        """Đảm bảo Chrome Auto trên port cố định đã sẵn sàng."""
        import subprocess
        import os

        port = self._get_cdp_port()
        ok, info = cdp_health_check(port)
        if ok:
            return True, info, port

        chrome_path = self._find_chrome_exe()
        if not chrome_path:
            return False, "Không tìm thấy chrome.exe trên hệ thống!", port

        debug_pids = self._list_debug_profile_chrome_pids()
        if debug_pids:
            self._terminate_debug_profile_chrome(debug_pids)

        os.makedirs(CHROME_DEBUG_PROFILE_DIR, exist_ok=True)
        subprocess.Popen(
            [
                chrome_path,
                f"--remote-debugging-port={port}",
                f"--user-data-dir={CHROME_DEBUG_PROFILE_DIR}",
                "--new-window",
                VNEDU_SSO_LOGIN_URL,
            ],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )

        for _ in range(30):
            time.sleep(0.5)
            ok, info = cdp_health_check(port)
            if ok:
                return True, info, port
        return False, f"Chrome đã mở nhưng port {port} chưa sẵn sàng sau 15s.", port

    def _on_launch_chrome_debug(self):
        """Mở Chrome Debug riêng của app với --remote-debugging-port.

        Quy trình:
        1. Tìm chrome.exe trên hệ thống
        2. Kiểm tra port đã listening chưa (nếu rồi thì kết nối luôn)
        3. Nếu có Chrome debug riêng của app đang treo: chỉ đóng instance đó
        4. Mở lại Chrome với debug flag + user-data-dir riêng
        5. Chờ port sẵn sàng → tự động kết nối
        """
        import subprocess
        import os

        port = self._get_cdp_port()

        # Bước 1: Kiểm tra port đã listening chưa
        ok, info = cdp_health_check(port)
        if ok:
            self._log(f"Chrome debug port {port} đã sẵn sàng, kết nối...", "info")
            self._cdp_connect_result(True, info, port)
            return

        # Bước 2: Tìm chrome.exe
        chrome_path = self._find_chrome_exe()
        if not chrome_path:
            self._log("Không tìm thấy chrome.exe trên hệ thống!", "error")
            self.lbl_cdp_status.config(
                text="Không tìm thấy Chrome. Cài Chrome hoặc dùng Sao chép lệnh.",
                foreground="red"
            )
            return

        self.lbl_cdp_status.config(text="⏳ Đang khởi động Chrome Debug...", foreground="orange")
        self.btn_launch_chrome.config(state="disabled")
        self.root.update_idletasks()

        def _launch_work():
            """Thread worker: chỉ dọn Chrome debug riêng của app → mở mới → chờ port."""
            try:
                ok_launch, info_launch, used_port = self._launch_or_reuse_chrome_auto_sync()
                self._post_ui(lambda: self._chrome_launch_result(ok_launch, info_launch, used_port))
            except Exception as e:
                self._post_ui(lambda e=e: self._chrome_launch_result(
                    False, f"Lỗi khởi động Chrome: {e}", port
                ))

        threading.Thread(target=_launch_work, daemon=True).start()

    def _chrome_launch_result(self, ok, info, port):
        """Callback sau khi Chrome debug được khởi động (UI thread).

        Args:
            ok: True nếu debug port đã sẵn sàng.
            info: Thông tin Chrome hoặc thông báo lỗi.
            port: Số port CDP.
        """
        self.btn_launch_chrome.config(state="normal")
        if ok:
            self._log(f"Chrome Debug đã sẵn sàng trên port {port}", "success")
            self._cdp_connect_result(True, info, port)
        else:
            self.lbl_cdp_status.config(text=f"✗ {info}", foreground="red")
            self._log(f"Launch Chrome failed: {info}", "error")

    def _copy_chrome_cmd(self):
        """Copy lệnh mở Chrome với CDP vào clipboard."""
        port = self._get_cdp_port()
        cmd = CHROME_LAUNCH_CMD.format(port=port, profile_dir=CHROME_DEBUG_PROFILE_DIR)
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(cmd)
            self.root.update_idletasks()
            self._log(f"Đã copy: {cmd}", "info")
        except Exception:
            self._log(f"Lệnh: {cmd}", "info")

    def _on_cdp_connect(self):
        """Kết nối Chrome CDP (chạy health check trong thread)."""
        if not _HAS_CDP:
            self._log("Module chrome_bridge.py không tìm thấy!", "error")
            return

        self.lbl_cdp_status.config(text="⏳ Đang kết nối...", foreground="orange")
        self.btn_cdp_connect.config(state="disabled")
        self.root.update_idletasks()

        port = self._get_cdp_port()

        def _check():
            ok, info = cdp_health_check(port)
            self._post_ui(lambda: self._cdp_connect_result(ok, info, port))

        threading.Thread(target=_check, daemon=True).start()

    def _cdp_connect_result(self, ok, info, port):
        """Callback sau health check (chạy trong UI thread)."""
        if ok:
            self._cdp_connected = True
            self._cdp_port = port
            self._mark_sched_form_session_changed(
                clear_cache=True,
                reason=(
                    "CDP vừa kết nối hoặc đổi session. Hãy bấm [Quét Form] lại "
                    "để nạp đúng Môn học / Phân môn trước khi chạy schedule."
                ),
                announce=True,
                log_level="info",
            )
            self._sched_teacher_progress_latest_week = None
            self.lbl_cdp_status.config(
                text=f"● Đã kết nối: {info}", foreground="green"
            )
            self.btn_cdp_connect.config(state="disabled")
            self.btn_cdp_disconnect.config(state="normal")
            self.btn_inspect.config(state="normal")
            self.btn_recover_web.config(state="normal")
            self.btn_discover_delete.config(state="normal")
            self.btn_sched_load_lop.config(state="normal")
            self._set_schedule_button_states(
                running=False,
                can_resume=bool(self._schedule_resume_state),
            )
            self._set_quick_prepare_button_state()
            self._set_auto_login_button_state()
            self._log(f"CDP Connected: {info}", "success")

            # Auto-load thông tin Tuần/Lớp hiện tại
            self._on_load_current_info()
            if self.var_sched_lop.get().strip():
                self._on_sched_progress_context_changed()
        else:
            self._cdp_connected = False
            self.lbl_cdp_status.config(
                text=f"✗ {info}", foreground="red"
            )
            self.btn_cdp_connect.config(state="normal")
            self.btn_recover_web.config(state="disabled")
            self._set_schedule_button_states(
                running=False,
                can_resume=bool(self._schedule_resume_state),
            )
            self._set_quick_prepare_button_state()
            self._set_auto_login_button_state()
            self._log(f"CDP Connect failed: {info}", "error")

    def _on_cdp_disconnect(self):
        """Ngắt kết nối CDP."""
        self._cdp_connected = False
        self._cancel_sched_teacher_progress_jobs()
        self._sched_teacher_progress_request_id += 1
        self._mark_sched_form_session_changed(
            clear_cache=True,
            reason=(
                "CDP đã ngắt kết nối. Khi kết nối lại, hãy bấm [Quét Form] trước "
                "khi chạy hoặc tiếp tục schedule."
            ),
        )
        self._sched_teacher_progress_latest_week = None
        self.lbl_cdp_status.config(text="○ Đã ngắt kết nối", foreground="gray")
        self.btn_cdp_connect.config(state="normal")
        self.btn_cdp_disconnect.config(state="disabled")
        self.btn_inspect.config(state="disabled")
        self.btn_recover_web.config(state="disabled")
        self.btn_discover_delete.config(state="disabled")
        self.btn_sched_load_lop.config(state="disabled")
        self.btn_sched_run.config(state="disabled")
        self.btn_sched_stop.config(state="disabled")
        self.btn_sched_resume.config(state="disabled")
        self._set_quick_prepare_button_state()
        self._set_auto_login_button_state()
        self.lbl_cdp_info.config(text="")
        self._render_sched_teacher_progress(None)
        self._log("CDP Disconnected", "info")

    def _on_load_current_info(self):
        """Đọc Tuần + Lớp hiện tại trên VnEdu (background thread)."""
        if not self._cdp_connected:
            return

        def _work():
            try:
                bridge = ChromeBridge(port=self._cdp_port)
                ok, msg = bridge.connect()
                if ok:
                    ok2, info = bridge.get_current_selection()
                    bridge.disconnect()
                    if ok2:
                        self._post_ui(lambda: self._show_current_info(info))
                else:
                    bridge.disconnect()
            except Exception as e:
                print(f"[CDP] Load current info error: {e}")

        threading.Thread(target=_work, daemon=True).start()

    def _show_current_info(self, info):
        """Hiển thị thông tin Tuần/Lớp hiện tại."""
        tuan = info.get("tuan", "?")
        lop = info.get("lop", "?")
        self.lbl_cdp_info.config(
            text=f"📌 Trang hiện tại: {tuan} — {lop}"
        )

    def _on_inspect_page(self):
        """Inspect cấu trúc trang VnEdu (background thread)."""
        if not self._cdp_connected:
            return

        self._log("🔍 Đang inspect trang VnEdu...", "info")

        def _work():
            try:
                bridge = ChromeBridge(port=self._cdp_port)
                ok, msg = bridge.connect()
                if ok:
                    ok2, data = bridge.inspect_page()
                    bridge.disconnect()
                    if ok2:
                        self._post_ui(lambda: self._show_inspect_result(data))
                    else:
                        self._post_ui(lambda: self._log(f"Inspect lỗi: {data}", "error"))
                else:
                    bridge.disconnect()
                    self._post_ui(lambda: self._log(f"Inspect connect lỗi: {msg}", "error"))
            except Exception as e:
                self._post_ui(lambda e=e: self._log(f"Inspect exception: {e}", "error"))

        threading.Thread(target=_work, daemon=True).start()

    def _show_inspect_result(self, data):
        """Hiển thị kết quả inspect."""
        self._log(f"📋 URL: {data.get('url', '?')}", "info")

        # ExtJS Comboboxes (VnEdu v5)
        ext_combos = data.get("extCombos", [])
        if ext_combos:
            self._log(f"📋 ExtJS Comboboxes ({len(ext_combos)}):", "info")
            for c in ext_combos:
                self._log(
                    f"   ✓ {c.get('id','')} name={c.get('name','')} "
                    f"store={c.get('storeCount',0)} current=\"{c.get('rawValue','')}\"",
                    "info"
                )

        # HTML <select> (fallback)
        selects = data.get("selects", [])
        if selects:
            self._log(f"📋 HTML Selects ({len(selects)}):", "info")
            for s in selects:
                vis = "✓" if s.get("visible") else "✗"
                self._log(
                    f"   {vis} #{s.get('id','')} name={s.get('name','')} "
                    f"opts={s.get('optionCount',0)} current={s.get('currentText','')}",
                    "info"
                )

        if not ext_combos and not selects:
            self._log("📋 Dropdowns (0): Không tìm thấy", "warning")

        # Tables
        tables = data.get("tables", [])
        self._log(f"📋 Tables ({len(tables)}):", "info")
        for t in tables:
            rs = "rowspan" if t.get("hasRowspan") else "flat"
            self._log(
                f"   #{t.get('id','')} class={t.get('className','')} "
                f"rows={t.get('rows',0)} [{rs}]",
                "info"
            )

        # Add buttons count
        add_count = data.get("addButtons", 0)
        if add_count > 0:
            self._log(f"📋 Nút ➕ (a.add_tiet_so_dau_bai): {add_count}", "info")

    def _format_vnedu_ui_state(self, data):
        """Tóm tắt trạng thái tương tác chính của trang VNEDU để log ngắn gọn."""
        if not isinstance(data, dict):
            return str(data)
        if data.get("khdh_checked") is True:
            mode = "Gợi ý KHBD/KHDH"
        elif data.get("view_checked") is True:
            mode = "Xem"
        else:
            mode = "không rõ"

        combo_bits = []
        for combo in list(data.get("combos") or []):
            name = str(combo.get("name", "") or "").strip()
            label = {
                "cboCapHoc": "Cấp",
                "cboTuanHoc": "Tuần",
                "cboLopHoc": "Lớp",
            }.get(name, name or "combo")
            raw = str(combo.get("raw", "") or "").strip() or "--"
            flags = []
            if combo.get("expanded"):
                flags.append("đang mở")
            if combo.get("disabled"):
                flags.append("disabled")
            if combo.get("readOnly"):
                flags.append("readOnly")
            flag_text = f" ({', '.join(flags)})" if flags else ""
            combo_bits.append(f"{label}={raw}{flag_text}")

        combo_text = "; ".join(combo_bits) if combo_bits else "không đọc được dropdown"
        return (
            f"mode={mode}; AJAX={'đang chạy' if data.get('ajax_loading') else 'rảnh'}; "
            f"mask={int(data.get('visible_masks') or 0)}; "
            f"popup_chặn={int(data.get('blocking_windows') or 0)}; "
            f"dropdown_mở={int(data.get('visible_boundlists') or 0)}; "
            f"bảng={'có' if data.get('has_sdb_table') else 'chưa thấy'}; {combo_text}"
        )

    def _on_recover_vnedu_ui(self):
        """Khôi phục trạng thái thao tác VNEDU sau automation bị kẹt."""
        if not self._cdp_connected:
            self._log("Chưa kết nối CDP!", "warning")
            return
        if (
            self._schedule_running
            or self._quick_prepare_running
            or self._class_stats_running
            or self._delete_running
            or getattr(self, "_delete_scanning", False)
            or getattr(self, "_auto_login_running", False)
        ):
            self._log(
                "Đang có worker dùng CDP. Chờ worker xong rồi mới khôi phục web.",
                "warning",
            )
            return

        self._log("Đang khôi phục trạng thái web VNEDU...", "info")
        self.btn_recover_web.config(state="disabled")

        def _work():
            bridge = None
            try:
                bridge = ChromeBridge(port=self._cdp_port)
                ok, msg = bridge.connect()
                if not ok:
                    self._post_ui(lambda: self._log(f"Khôi phục web connect lỗi: {msg}", "error"))
                    return

                ok_cleanup, msg_cleanup = bridge.cleanup_after_automation(restore_view_mode=True)
                ok_state, state = bridge.diagnose_ui_state()
                if ok_cleanup:
                    self._post_ui(lambda: self._log(f"Đã khôi phục web: {msg_cleanup}", "success"))
                else:
                    self._post_ui(lambda: self._log(f"Khôi phục web lỗi: {msg_cleanup}", "warning"))
                if ok_state:
                    self._post_ui(lambda: self._log(
                        "Trạng thái web sau khôi phục: " + self._format_vnedu_ui_state(state),
                        "info",
                    ))
                else:
                    self._post_ui(lambda: self._log(f"Không đọc được trạng thái sau khôi phục: {state}", "warning"))
            except Exception as e:
                self._post_ui(lambda e=e: self._log(
                    f"Khôi phục web exception: {type(e).__name__}: {str(e)[:120]}",
                    "error",
                ))
            finally:
                if bridge:
                    try:
                        bridge.disconnect()
                    except Exception:
                        pass
                self._post_ui(lambda: self.btn_recover_web.config(
                    state="normal" if self._cdp_connected else "disabled"
                ))

        threading.Thread(target=_work, daemon=True).start()

    def _on_discover_delete_controls(self):
        """[READ-ONLY] Dò cấu trúc nút Xóa trên dòng đã có dữ liệu (background thread)."""
        if not self._cdp_connected:
            self._log("Chưa kết nối CDP!", "warning")
            return

        self._log("🧪 Đang dò cấu trúc nút Xóa (chỉ đọc, không click)...", "info")

        def _work():
            try:
                bridge = ChromeBridge(port=self._cdp_port)
                ok, msg = bridge.connect()
                if not ok:
                    bridge.disconnect()
                    self._post_ui(lambda: self._log(f"Kiểm tra nút xóa connect lỗi: {msg}", "error"))
                    return
                ok2, data = bridge.discover_delete_controls(max_rows=8)
                bridge.disconnect()
                if ok2:
                    self._post_ui(lambda: self._show_delete_discovery_result(data))
                else:
                    self._post_ui(lambda: self._log(f"Kiểm tra nút xóa lỗi: {data}", "error"))
            except Exception as e:
                self._post_ui(lambda e=e: self._log(f"Kiểm tra nút xóa exception: {e}", "error"))

        threading.Thread(target=_work, daemon=True).start()

    def _show_delete_discovery_result(self, data):
        """Log chi tiết các control tìm được để xác định selector nút Xóa thật."""
        self._log(
            f"🧪 Bảng: id='{data.get('table_id','')}' class='{data.get('table_class','')}'",
            "info",
        )

        global_selectors = data.get("global_selectors", {}) or {}
        if global_selectors:
            self._log("🧪 Thống kê control trong bảng (class/onclick fn → số lượng):", "info")
            for key, count in sorted(
                global_selectors.items(), key=lambda kv: kv[1], reverse=True
            ):
                self._log(f"   • [{count}x] {key}", "info")

        rows = data.get("rows", []) or []
        if not rows:
            self._log(
                "🧪 Không thấy dòng nào có control. Hãy mở Chi tiết sổ đầu bài "
                "của lớp/tuần ĐÃ CÓ dữ liệu rồi bấm lại.",
                "warning",
            )
            return

        for row in rows:
            row_idx = row.get("rowIdx", "?")
            texts = " | ".join(row.get("row_texts", [])[:8])
            attrs = row.get("action_cell_attrs", {}) or {}
            attr_summary = ", ".join(
                f"{k}={v}" for k, v in attrs.items()
                if k in ("thu", "buoi", "tiet", "mon_hoc_id", "phan_mon_id",
                         "tiet_ppct", "id", "so_dau_bai_id", "chi_tiet_id")
            )
            self._log(f"🧪 Row#{row_idx}: {texts}", "info")
            if attr_summary:
                self._log(f"     action_cell attrs: {attr_summary}", "info")
            for ctrl in row.get("controls", []):
                tag = ctrl.get("tag", "")
                cls = ctrl.get("className", "")
                onclick = ctrl.get("onclick", "")
                href = ctrl.get("href", "")
                title = ctrl.get("title", "")
                color = ctrl.get("color", "")
                data_attrs = ctrl.get("dataAttrs", {}) or {}
                bits = [f"<{tag}>"]
                if cls:
                    bits.append(f"class='{cls}'")
                if title:
                    bits.append(f"title='{title}'")
                if onclick:
                    bits.append(f"onclick=\"{onclick}\"")
                if href and href != "#":
                    bits.append(f"href='{href}'")
                if color:
                    bits.append(f"color={color}")
                if data_attrs:
                    bits.append("attrs=" + ", ".join(f"{k}={v}" for k, v in data_attrs.items()))
                self._log("       ↳ " + " ".join(bits), "info")
        self._log(
            "🧪 XONG. Hãy gửi lại log này để tôi xác định selector nút Xóa chính xác "
            "trước khi viết chức năng xóa.",
            "success",
        )

    def _on_quick_prepare(self):
        """Chạy tuần tự Inspect -> Tải lớp -> Quét form bằng một bridge."""
        if not self._cdp_connected:
            self._log("Chưa kết nối CDP!", "warning")
            return
        if self._schedule_running:
            self._log("Schedule đang chạy, không thể chuẩn bị nhanh cùng lúc.", "warning")
            return
        if self._quick_prepare_running:
            self._log("Chuẩn bị nhanh đang chạy rồi.", "warning")
            return

        self._quick_prepare_running = True
        self._set_quick_prepare_button_state()
        self.btn_inspect.config(state="disabled")
        self.btn_sched_load_lop.config(state="disabled")
        self.btn_sched_scan_form.config(state="disabled")
        self._set_sched_live_progress(
            current=0,
            total=3,
            phase="Quét full dữ liệu",
            detail="Chuẩn bị Inspect / Tải lớp / Quét Form",
            state="running",
        )
        self._log("🟢 Đang chuẩn bị nhanh: Inspect -> Tải lớp -> Quét form...", "info")

        def _finish():
            self._quick_prepare_running = False
            if self._cdp_connected:
                self.btn_inspect.config(state="normal")
                self.btn_sched_load_lop.config(state="normal")
                self.btn_sched_scan_form.config(state="normal")
            self._set_quick_prepare_button_state()

        def _work():
            bridge = None
            inspect_data = None
            lop_options = None
            form_data = None
            form_data_from_cache = False
            form_selection_info = None
            errors = []
            try:
                bridge = ChromeBridge(port=self._cdp_port)
                ok, msg = bridge.connect()
                if not ok:
                    errors.append(f"Kết nối CDP lỗi: {msg}")
                else:
                    ok_ready, ready_msg = self._ensure_bridge_detail_ready(
                        bridge,
                        "Quét dữ liệu lớp/tuần",
                    )
                    if not ok_ready:
                        errors.append(ready_msg)
                        return
                    ok_inspect, inspect_payload = bridge.inspect_page()
                    if ok_inspect:
                        inspect_data = inspect_payload
                        self._post_ui(lambda: self._set_sched_live_progress(
                            current=1,
                            total=3,
                            phase="Quét dữ liệu",
                            detail="Inspect xong, đang tải danh sách lớp",
                            state="running",
                        ))
                    else:
                        errors.append(f"Inspect lỗi: {inspect_payload}")

                    ok_lop, lop_payload = bridge.discover_lop_options_for_weeks()
                    if ok_lop:
                        lop_options = self._sort_lop_options(lop_payload.get("options", []))
                        self._post_ui(lambda: self._set_sched_live_progress(
                            current=2,
                            total=3,
                            phase="Quét full dữ liệu",
                            detail="Đã tải lớp, đang quét Môn học / Phân môn",
                            state="running",
                        ))
                    else:
                        errors.append(f"Tải DS Lớp lỗi: {lop_payload}")

                    ok_info, current_info = bridge.get_current_selection()
                    cached_form = self._get_sched_form_options_cache(current_info) if ok_info else None
                    if cached_form is not None:
                        form_data = cached_form
                        form_data_from_cache = True
                        form_selection_info = current_info if ok_info else None
                    else:
                        ok_form, form_payload = bridge.read_form_options(row_index=0)
                        if ok_form:
                            form_data = form_payload
                            form_selection_info = current_info if ok_info else None
                            if ok_info:
                                self._set_sched_form_options_cache(current_info, form_payload)
                        else:
                            errors.append(f"Quét form lỗi: {form_payload}")
            except Exception as e:
                errors.append(
                    f"Chuẩn bị nhanh exception: {type(e).__name__}: {str(e)[:140]}"
                )
            finally:
                if bridge:
                    try:
                        bridge.disconnect()
                    except Exception:
                        pass

                def _apply_results():
                    if inspect_data is not None:
                        self._show_inspect_result(inspect_data)
                    if lop_options is not None:
                        self._populate_sched_lop(lop_options)
                    if form_data is not None:
                        self._populate_sched_phan_mon(form_data, form_selection_info)
                        if form_data_from_cache:
                            self._log("Quét dữ liệu: dùng cache Quét form hiện có.", "info")

                    if errors:
                        if form_data is None:
                            self._set_sched_form_rescan_reason(
                                "Chuẩn bị nhanh chưa lấy được dữ liệu Môn học / Phân môn. "
                                "Hãy mở đúng màn sổ đầu bài của lớp hiện tại rồi bấm [Quét Form] lại."
                            )
                        for item in errors:
                            self._log(item, "error")
                        if inspect_data is not None or lop_options is not None or form_data is not None:
                            self._log("⚠ Chuẩn bị nhanh hoàn tất nhưng có bước lỗi.", "warning")
                        self._set_sched_live_progress(
                            current=(
                                int(inspect_data is not None)
                                + int(lop_options is not None)
                                + int(form_data is not None)
                            ),
                            total=3,
                            phase="Quét full dữ liệu",
                            detail="Có bước lỗi. Kiểm tra log để biết mục cần làm lại",
                            state="error",
                        )
                    else:
                        self._log(
                            "✅ Chuẩn bị nhanh hoàn tất: Inspect, Tải lớp, Quét form",
                            "success",
                        )
                        self._set_sched_live_progress(
                            current=3,
                            total=3,
                            phase="Quét full dữ liệu",
                            detail="Hoàn tất Inspect, tải lớp và Quét Form",
                            state="success",
                        )
                    _finish()

                self._post_ui(_apply_results)

        threading.Thread(target=_work, daemon=True).start()

    def _resolve_schedule_params_from_form_data(self, request_data, form_data):
        """Resolve ID Môn học / Phân môn từ dữ liệu form quét được sau auto-navigation."""
        mon_hoc_options = list(form_data.get("mon_hoc") or [])
        phan_mon_options_all = list(form_data.get("phan_mon") or [])
        raw_pm_map = form_data.get("phan_mon_by_mon_hoc", {}) or {}
        phan_mon_by_mon_hoc = {
            str(key): list(options or [])
            for key, options in raw_pm_map.items()
            if key is not None
        }

        mon_hoc_option = self._resolve_sched_option(
            mon_hoc_options, request_data.get("mon_hoc_text", "")
        )
        if mon_hoc_option is None:
            return None, (
                f"Không resolve được Môn học '{request_data.get('mon_hoc_text', '')}' "
                "từ dữ liệu form sau khi auto đăng nhập. "
                "Hãy mở đúng màn sổ đầu bài rồi bấm [Quét Form] lại."
            )

        mon_hoc_value = mon_hoc_option["value"]
        phan_mon_options = self._select_sched_phan_mon_options(
            mon_hoc_value,
            phan_mon_by_mon_hoc,
            phan_mon_options_all,
        )
        if not phan_mon_options:
            return None, (
                f"Không tải được Phân môn chuyên biệt cho Môn học "
                f"'{mon_hoc_option.get('text', '')}'. Hãy bấm [Quét Form] lại trên đúng lớp và đúng màn VnEdu."
            )
        phan_mon_option = self._resolve_sched_option(
            phan_mon_options, request_data.get("phan_mon_text", "")
        )
        if phan_mon_option is None:
            return None, (
                f"Không resolve được Phân môn '{request_data.get('phan_mon_text', '')}' "
                f"cho Môn học '{mon_hoc_option.get('text', '')}'. "
                "Nếu danh sách vừa bị lệch sau khi đổi lớp/màn VnEdu, hãy bấm [Quét Form] lại."
            )

        tuan_count = int(request_data["tuan_to"]) - int(request_data["tuan_from"]) + 1
        params = {
            "port": self._get_cdp_port(),
            "tuan_from": int(request_data["tuan_from"]),
            "tuan_to": int(request_data["tuan_to"]),
            "lop": str(request_data["lop"]),
            "slots": list(request_data.get("slots") or []),
            "hs_nghi": str(request_data.get("hs_nghi", "0")),
            "diem": str(request_data.get("diem", "10")),
            "nhan_xet_raw": str(request_data.get("nhan_xet_raw", "")),
            "phan_mon_value": phan_mon_option["value"],
            "phan_mon_text": phan_mon_option["text"],
            "mon_hoc_value": mon_hoc_value,
            "mon_hoc_text": mon_hoc_option["text"],
            "mon_hoc_field": form_data.get("mon_hoc_field"),
            "ppct_start": int(request_data["ppct_start"]),
            "planned_total": tuan_count * len(request_data.get("slots") or []),
        }
        return params, ""

    def _on_auto_login_and_run(self):
        """Tự mở Chrome Auto, đăng nhập VnEdu và vào Chi tiết sổ đầu bài."""
        if self._schedule_running:
            self._log("Schedule đang chạy, không thể auto-login cùng lúc.", "warning")
            return
        if self._auto_login_running:
            self._log("Auto-login đang chạy rồi.", "warning")
            return
        if self._delete_running or self._delete_scanning:
            messagebox.showwarning(
                "Cảnh báo",
                "Đang có thao tác Xóa dữ liệu chạy. Hãy chờ xong rồi mới auto-login.",
            )
            return
        if self._class_stats_thread and self._class_stats_thread.is_alive():
            messagebox.showwarning(
                "Cảnh báo",
                "Đang có phiên thống kê lớp sử dụng CDP. Hãy chờ xong rồi mới chạy auto-login.",
            )
            return

        username = self.var_vnedu_username.get().strip()
        password = self.var_vnedu_password.get()

        self._auto_login_running = True
        self._set_auto_login_button_state()
        self._set_sched_live_progress(
            current=0,
            total=3,
            phase="Auto-login",
            detail="Chuẩn bị Chrome Auto",
            state="running",
        )
        self._log("🟢 Bắt đầu auto-login và điều hướng tới Chi tiết sổ đầu bài...", "info")

        def _work():
            bridge = None
            connect_info = ""
            nav_info = None
            errors = []
            try:
                ok_launch, launch_info, port = self._launch_or_reuse_chrome_auto_sync()
                if not ok_launch:
                    errors.append(f"Chrome Auto lỗi: {launch_info}")
                    return
                connect_info = launch_info
                self._post_ui(lambda: self._set_sched_live_progress(
                    current=1,
                    total=3,
                    phase="Auto-login",
                    detail="Chrome Auto đã sẵn sàng, đang nối CDP",
                    state="running",
                ))

                bridge = ChromeBridge(port=port)
                ok_connect, msg_connect = bridge.connect(allow_any_tab=True)
                if not ok_connect:
                    errors.append(f"Kết nối CDP lỗi: {msg_connect}")
                    return
                self._post_ui(lambda: self._set_sched_live_progress(
                    current=2,
                    total=3,
                    phase="Auto-login",
                    detail="Đã nối CDP, đang điều hướng tới Chi tiết sổ đầu bài",
                    state="running",
                ))

                ok_nav, nav_payload = bridge.ensure_chi_tiet_sodau_bai(
                    username,
                    password,
                )
                if not ok_nav:
                    errors.append(str(nav_payload))
                    return
                nav_info = nav_payload
            except Exception as e:
                errors.append(f"Auto-login exception: {type(e).__name__}: {str(e)[:180]}")
            finally:
                if bridge:
                    try:
                        bridge.disconnect()
                    except Exception:
                        pass

                def _apply_results():
                    if connect_info:
                        self._cdp_connect_result(True, connect_info, self._get_cdp_port())
                    if nav_info:
                        self._log(
                            f"✅ Đã vào Chi tiết sổ đầu bài: {nav_info.get('url', '')}",
                            "success",
                        )

                    self._auto_login_running = False
                    self._set_auto_login_button_state()

                    if errors:
                        for item in errors:
                            self._log(item, "error")
                        self.lbl_sched_progress.config(text="✗ Auto-login/điều hướng chưa hoàn tất")
                        self._set_sched_live_progress(
                            current=0,
                            total=3,
                            phase="Auto-login",
                            detail="Chưa hoàn tất. Kiểm tra log để biết bước bị lỗi",
                            state="error",
                        )
                        return

                    self._log(
                        "✅ Auto-login hoàn tất. Bạn đang ở màn Chi tiết sổ đầu bài.",
                        "success",
                    )
                    self.lbl_sched_progress.config(
                        text="✅ Đã vào Chi tiết sổ đầu bài — sẵn sàng cho bước quét/nhập thủ công"
                    )
                    self._set_sched_live_progress(
                        current=3,
                        total=3,
                        phase="Auto-login",
                        detail="Đã vào màn Chi tiết sổ đầu bài",
                        state="success",
                    )

                self._post_ui(_apply_results)

        self._auto_login_thread = threading.Thread(target=_work, daemon=True)
        self._auto_login_thread.start()

    # -----------------------------------------------------------------
    # BATCH LỚP CONTROLLER
    # -----------------------------------------------------------------

    # -----------------------------------------------------------------
    # SCHEDULE — QUÉT & NHẬP THEO LỊCH DẠY (CDP SCAN + PYAUTOGUI FILL)
    # -----------------------------------------------------------------

    def _launch_schedule_worker(self, params, resume=False):
        """Khởi chạy worker schedule với state UI phù hợp cho run mới hoặc resume."""
        planned_total = max(int(params.get("planned_total", 1) or 1), 1)
        resume_state = params.get("resume_state") or {}
        schedule_mode = str(
            params.get("schedule_mode", SCHEDULE_MODE_MANUAL) or SCHEDULE_MODE_MANUAL
        )
        base_params = copy.deepcopy({k: v for k, v in params.items() if k != "resume_state"})
        processed_count = (
            int(resume_state.get("completed", 0) or 0)
            + int(resume_state.get("skipped", 0) or 0)
            + int(resume_state.get("errors", 0) or 0)
        )
        next_ppct = resume_state.get("next_ppct", params.get("ppct_start"))
        last_success_ppct = resume_state.get("last_success_ppct")

        if not resume:
            self._schedule_results = []
            initial_resume_state = self._build_initial_schedule_resume_state(params)
            self._update_schedule_resume_snapshot(
                resume_state=initial_resume_state,
                params=base_params,
            )
            self._schedule_last_summary = None
        elif not self._schedule_resume_params:
            self._update_schedule_resume_snapshot(
                resume_state=copy.deepcopy(resume_state) if resume_state else self._build_initial_schedule_resume_state(params),
                params=copy.deepcopy({k: v for k, v in params.items() if k != "resume_state"}),
            )
        else:
            self._update_schedule_resume_snapshot(
                resume_state=copy.deepcopy(resume_state) if resume_state else self._schedule_resume_state,
                params=base_params,
            )

        if self._schedule_summary_window and self._schedule_summary_window.winfo_exists():
            try:
                self._schedule_summary_window.destroy()
            except Exception:
                pass

        self._schedule_running = True
        self._schedule_stop_event.clear()
        self._schedule_stop_reason = None
        self._set_schedule_button_states(running=True, can_resume=False)
        self._set_auto_login_button_state()
        self.sched_progressbar["maximum"] = planned_total
        self.sched_progressbar["value"] = min(processed_count, planned_total)
        self._set_sched_live_progress(
            current=processed_count,
            total=planned_total,
            phase="Schedule",
            detail="Đang khởi chạy worker và đồng bộ checkpoint",
            state="running",
        )

        tuan_count = int(params["tuan_to"]) - int(params["tuan_from"]) + 1
        mode_text = "tiếp tục" if resume else "bắt đầu"
        if schedule_mode == SCHEDULE_MODE_KHDH:
            lop_list = list(params.get("lop_list") or [params.get("lop")])
            lop_desc = (
                str(lop_list[0])
                if len(lop_list) <= 1
                else f"{len(lop_list)} lớp ({', '.join(str(x) for x in lop_list[:3])}"
                + (f"... +{len(lop_list) - 3}" if len(lop_list) > 3 else "")
                + ")"
            )
            self.lbl_sched_progress.config(
                text=f"🚀 Schedule {mode_text}: {tuan_count} tuần — {lop_desc} — mode KHDH"
            )
        else:
            self.lbl_sched_progress.config(
                text=(
                    f"🚀 Schedule {mode_text}: {tuan_count} tuần × "
                    f"{len(params['slots'])} tiết/tuần — Lớp {params['lop']}"
                )
            )
        self._update_sched_ppct_runtime(
            last_success=last_success_ppct,
            next_ppct=next_ppct,
            status="running",
        )

        worker_target = self._schedule_worker_khdh if schedule_mode == SCHEDULE_MODE_KHDH else self._schedule_worker
        self._schedule_thread = threading.Thread(
            target=worker_target, args=(params,), daemon=True
        )
        self._schedule_thread.start()
        self.root.after(100, self._poll_schedule_queue)

    def _on_schedule_run(self):
        """Bắt đầu quét & nhập theo lịch dạy từ đầu."""
        if not self._cdp_connected:
            self._log("Chưa kết nối CDP!", "error")
            return
        if self._delete_running or self._delete_scanning:
            messagebox.showwarning(
                "Cảnh báo",
                "Đang có thao tác Xóa dữ liệu chạy. Hãy chờ xong rồi mới chạy schedule.",
            )
            return
        if self._class_stats_thread and self._class_stats_thread.is_alive():
            messagebox.showwarning(
                "Cảnh báo",
                "Đang có phiên thống kê lớp sử dụng CDP. Hãy chờ xong rồi mới chạy schedule.",
            )
            return

        if self._schedule_running:
            self._log("Schedule đang chạy!", "warning")
            return

        schedule_mode = self._get_sched_mode()
        lop_list = self._get_sched_lop_list(schedule_mode)
        sched_lop = lop_list[0] if lop_list else ""
        if not sched_lop:
            self._log("Chưa chọn Lớp!", "warning")
            messagebox.showwarning("Cảnh báo", "Chưa chọn Lớp trong panel Lịch dạy.")
            return

        try:
            tuan_from = self.var_sched_tuan_from.get()
            tuan_to = self.var_sched_tuan_to.get()
        except (tk.TclError, ValueError):
            self._log("Tuần không hợp lệ!", "error")
            return
        if tuan_from > tuan_to:
            tuan_from, tuan_to = tuan_to, tuan_from

        hs_nghi = self.var_sched_hs_nghi.get() or "0"
        diem = self.var_sched_diem.get() or "10"
        nhan_xet_raw = self.var_sched_nhan_xet.get() or "Lớp học chăm ngoan"
        tuan_count = tuan_to - tuan_from + 1
        params = {
            "port": self._cdp_port,
            "tuan_from": tuan_from,
            "tuan_to": tuan_to,
            "lop": sched_lop,
            "lop_list": lop_list,
            "hs_nghi": hs_nghi,
            "diem": diem,
            "nhan_xet_raw": nhan_xet_raw,
            "schedule_mode": schedule_mode,
        }

        if schedule_mode == SCHEDULE_MODE_KHDH:
            khdh_invalid_reason = self._get_sched_mode_invalid_reason(SCHEDULE_MODE_KHDH)
            if khdh_invalid_reason:
                self._show_sched_form_blocked_warning(
                    "Chưa sẵn sàng chạy schedule KHDH",
                    khdh_invalid_reason,
                )
                return
            ok_preflight, preflight_msg = self._preflight_khdh_schedule_context(self._cdp_port)
            if not ok_preflight:
                self._show_sched_form_blocked_warning(
                    "Trang live chưa sẵn sàng cho KHDH",
                    preflight_msg,
                )
                return
            est_total = max(tuan_count * max(len(lop_list), 1), 1)
            lop_desc = sched_lop if len(lop_list) == 1 else f"{len(lop_list)} lớp"
            self.lbl_sched_progress.config(
                text=f"🚀 {tuan_count} tuần — {lop_desc} — mode KHDH"
            )
            self._log(
                f"📅 Schedule KHDH bắt đầu: Tuần {tuan_from}→{tuan_to}, "
                f"{lop_desc} ({', '.join(lop_list)}). "
                "App sẽ quét toàn bộ row chữ đỏ theo KHDH của từng tuần.",
                "info",
            )
            params.update({
                "slots": [],
                "ppct_start": 0,
                "planned_total": est_total,
            })
        else:
            slots = self._get_schedule_slots()
            if not slots:
                self._log("Chưa cấu hình Lịch dạy! Check ít nhất 1 tiết.", "warning")
                messagebox.showwarning(
                    "Cảnh báo",
                    "Chưa cấu hình tiết nào trong Lịch dạy.\n"
                    "Hãy chọn Buổi và check các tiết cần nhập."
                )
                return

            subject_selection, subject_error = self._resolve_current_sched_subject_selection()
            if subject_selection is None:
                self._show_sched_form_blocked_warning(
                    "Chưa sẵn sàng chạy schedule",
                    subject_error,
                )
                return

            try:
                ppct_start = self.var_sched_ppct_start.get()
            except (tk.TclError, ValueError):
                ppct_start = 1

            est_total = tuan_count * len(slots)
            slots_desc = ", ".join(
                f"T{s['thu']}{s['buoi'][0]}T{s['tiet']}" for s in slots[:6]
            )
            if len(slots) > 6:
                slots_desc += f"... (+{len(slots)-6})"
            self.lbl_sched_progress.config(
                text=f"🚀 {tuan_count} tuần × {len(slots)} tiết/tuần — Lớp {sched_lop}"
            )
            self._log(
                f"📅 Schedule bắt đầu: Tuần {tuan_from}→{tuan_to}, "
                f"Lớp {sched_lop}, Slots: {slots_desc}",
                "info"
            )

            params.update({
                "slots": slots,
                "ppct_start": ppct_start,
                "planned_total": est_total,
            })
            params.update(subject_selection)
        self._launch_schedule_worker(params, resume=False)

    def _on_schedule_resume(self):
        """Tiếp tục schedule từ checkpoint gần nhất, không chạy lại từ đầu."""
        if not self._cdp_connected:
            self._log("Chưa kết nối CDP!", "error")
            return
        if self._delete_running or self._delete_scanning:
            messagebox.showwarning(
                "Cảnh báo",
                "Đang có thao tác Xóa dữ liệu chạy. Hãy chờ xong rồi mới tiếp tục schedule.",
            )
            return
        if self._class_stats_thread and self._class_stats_thread.is_alive():
            messagebox.showwarning(
                "Cảnh báo",
                "Đang có phiên thống kê lớp sử dụng CDP. Hãy chờ xong rồi mới tiếp tục schedule.",
            )
            return
        if self._schedule_running:
            self._log("Schedule đang chạy!", "warning")
            return
        if not self._schedule_resume_state or not self._schedule_resume_params:
            messagebox.showinfo("Thông báo", "Không có checkpoint nào để tiếp tục.")
            return

        params = copy.deepcopy(self._schedule_resume_params)
        schedule_mode = str(
            params.get("schedule_mode", SCHEDULE_MODE_MANUAL) or SCHEDULE_MODE_MANUAL
        )
        if schedule_mode != self._get_sched_mode():
            try:
                self.var_sched_mode.set(schedule_mode)
            except Exception:
                pass
            self._apply_sched_mode_state()
        if schedule_mode != SCHEDULE_MODE_KHDH:
            subject_selection, subject_error = self._resolve_current_sched_subject_selection()
            if subject_selection is None:
                self._show_sched_form_blocked_warning(
                    "Chưa sẵn sàng tiếp tục schedule",
                    subject_error,
                )
                return
            params.update(subject_selection)
        else:
            khdh_invalid_reason = self._get_sched_mode_invalid_reason(SCHEDULE_MODE_KHDH)
            if khdh_invalid_reason:
                self._show_sched_form_blocked_warning(
                    "Chưa sẵn sàng tiếp tục schedule KHDH",
                    khdh_invalid_reason,
                )
                return
            ok_preflight, preflight_msg = self._preflight_khdh_schedule_context(self._cdp_port)
            if not ok_preflight:
                self._show_sched_form_blocked_warning(
                    "Trang live chưa sẵn sàng cho KHDH",
                    preflight_msg,
                )
                return
        params["resume_state"] = copy.deepcopy(self._schedule_resume_state)
        next_lop_idx = int(params["resume_state"].get("next_lop_idx", 0) or 0)
        lop_list = list(params.get("lop_list") or [params.get("lop")])
        next_lop_text = (
            str(lop_list[next_lop_idx])
            if 0 <= next_lop_idx < len(lop_list)
            else str(params.get("lop", ""))
        )
        next_ppct = params["resume_state"].get("next_ppct")
        next_tuan = params["resume_state"].get("next_tuan_num")
        next_slot_idx = params["resume_state"].get("next_slot_idx", 0)
        next_row_key = params["resume_state"].get("next_row_key")
        if schedule_mode == SCHEDULE_MODE_KHDH:
            self._log(
                f"↻ Tiếp tục schedule KHDH từ Lớp {next_lop_text}, Tuần {next_tuan}, "
                f"row gợi ý #{next_slot_idx + 1}"
                + (f" | key={next_row_key}" if next_row_key else ""),
                "info",
            )
        else:
            self._log(
                f"↻ Tiếp tục schedule từ Tuần {next_tuan}, slot #{next_slot_idx + 1}, "
                f"PPCT nội bộ {next_ppct}",
                "info",
            )
        self._launch_schedule_worker(params, resume=True)

    def _request_schedule_stop(self, source="nút Dừng"):
        """Yêu cầu dừng schedule tại checkpoint an toàn tiếp theo."""
        if not self._schedule_running:
            return False
        if self._schedule_stop_event.is_set():
            return True

        self._schedule_stop_reason = source
        self._schedule_stop_event.set()
        self.btn_sched_stop.config(state="disabled")
        self.lbl_sched_progress.config(text="⏸ Đang dừng an toàn sau slot hiện tại...")
        self._set_sched_live_progress(
            current=None,
            total=None,
            phase="Schedule",
            detail="Đang dừng an toàn sau slot hiện tại",
            state="paused",
        )
        self._log(f"⏹ Đang dừng schedule ({source})...", "warning")
        return True

    def _on_schedule_stop(self):
        """Dừng schedule qua nút bấm."""
        self._request_schedule_stop("nút Dừng")

    def _on_hotkey_escape(self, _event=None):
        """Hotkey Esc: dừng schedule ngay tại checkpoint an toàn gần nhất."""
        if not self._schedule_running:
            return None
        self._request_schedule_stop("phím Esc")
        return "break"

    def _schedule_worker_khdh(self, params):
        """Worker thread cho mode KHDH: quét row đỏ live rồi fill theo dữ liệu gợi ý."""
        bridge = None
        q = self._schedule_queue
        tuan_from = int(params["tuan_from"])
        tuan_to = int(params["tuan_to"])
        lop_list = [
            str(item or "").strip()
            for item in list(params.get("lop_list") or [params.get("lop")])
            if str(item or "").strip()
        ]
        if not lop_list:
            lop_list = [str(params.get("lop", "")).strip()]
        resume_state = params.get("resume_state") or {}
        completed = int(resume_state.get("completed", 0) or 0)
        skipped = int(resume_state.get("skipped", 0) or 0)
        errors = int(resume_state.get("errors", 0) or 0)
        last_success_ppct = resume_state.get("last_success_ppct")
        start_lop_idx = int(resume_state.get("next_lop_idx", 0) or 0)
        start_tuan_num = int(resume_state.get("next_tuan_num", tuan_from) or tuan_from)
        start_slot_idx = int(resume_state.get("next_slot_idx", 0) or 0)
        start_row_key = str(resume_state.get("next_row_key", "") or "").strip()
        stopped = False
        checkpoint_state = {
            "next_lop_idx": start_lop_idx,
            "next_tuan_num": start_tuan_num,
            "next_slot_idx": start_slot_idx,
            "next_row_key": start_row_key or None,
            "next_ppct": None,
            "completed": completed,
            "skipped": skipped,
            "errors": errors,
            "last_success_ppct": last_success_ppct,
        }

        hs_nghi = params["hs_nghi"]
        diem = params["diem"]
        nhan_xet_raw = params["nhan_xet_raw"]
        nhan_xet_items = [x.strip() for x in nhan_xet_raw.split("|") if x.strip()]
        if not nhan_xet_items:
            nhan_xet_items = ["Lớp học chăm ngoan"]

        def _digits(value):
            text = str(value or "").strip()
            match = re.search(r"\d+", text)
            return match.group() if match else ""

        def _normalize_text(value):
            text = str(value or "").replace("\n", " ")
            text = unicodedata.normalize("NFD", text)
            text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
            return re.sub(r"\s+", " ", text).strip().casefold()

        def _normalize_date_token(value):
            text = str(value or "").strip()
            match = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", text)
            if not match:
                return ""
            day, month, year = match.group(1).split("/")
            return f"{int(day):02d}/{int(month):02d}/{year}"

        def _build_checkpoint(next_lop_idx, next_tuan_num, next_slot_idx, next_row_key=None):
            return {
                "next_lop_idx": int(next_lop_idx),
                "next_tuan_num": int(next_tuan_num),
                "next_slot_idx": int(next_slot_idx),
                "next_row_key": str(next_row_key or "").strip() or None,
                "next_ppct": None,
                "completed": int(completed),
                "skipped": int(skipped),
                "errors": int(errors),
                "last_success_ppct": last_success_ppct,
            }

        def _row_resume_key(row_info):
            parts = [
                str(row_info.get("thu", "") or "").strip(),
                str(row_info.get("buoi", "") or "").strip().casefold(),
                str(row_info.get("tiet", "") or "").strip(),
                str(row_info.get("mon_hoc_id", "") or "").strip(),
                str(row_info.get("phan_mon_id", "") or "").strip(),
                _digits(row_info.get("ppct_hint")),
                _normalize_text(row_info.get("noi_dung_hint", "")),
            ]
            return "|".join(parts)

        def _next_checkpoint_after_week(lop_idx, tuan_num):
            if int(tuan_num) < tuan_to:
                return _build_checkpoint(lop_idx, int(tuan_num) + 1, 0, None)
            return _build_checkpoint(int(lop_idx) + 1, tuan_from, 0, None)

        def _emit_slot_result(status, lop_text, tuan_num, row_info, message, ppct_value=None):
            q.put(("slot_result", {
                "status": status,
                "week_num": int(tuan_num),
                "week_text": f"Tuần {tuan_num}",
                "slot_label": (
                    f"Lớp {lop_text} | "
                    + self._format_schedule_slot_label(
                        row_info.get("thu", "?"),
                        row_info.get("buoi", "?"),
                        row_info.get("tiet", "?"),
                    )
                ),
                "ppct": ppct_value,
                "message": message,
            }))

        def _emit_system_result(status, lop_text, tuan_num, message):
            q.put(("slot_result", {
                "status": status,
                "week_num": int(tuan_num),
                "week_text": f"Tuần {tuan_num}",
                "slot_label": f"Lớp {lop_text} | Quét row đỏ KHDH",
                "ppct": None,
                "message": message,
            }))

        def _mark_cdp_closed_stop(lop_idx, tuan_num, row_idx, message):
            nonlocal errors, stopped, checkpoint_state
            errors += 1
            stopped = True
            checkpoint_state = _build_checkpoint(lop_idx, tuan_num, row_idx, None)
            if not self._schedule_stop_reason:
                self._schedule_stop_reason = "CDP/Chrome đã đóng hoặc tab VnEdu không còn khả dụng"
            q.put(("checkpoint", checkpoint_state))
            q.put(("error", (
                "CDP/Chrome đã đóng hoặc tab VnEdu không còn khả dụng. "
                f"Worker KHDH dừng tại Lớp {lop_list[lop_idx] if 0 <= lop_idx < len(lop_list) else '?'}, "
                f"Tuần {tuan_num}. Chi tiết: {message}"
            )))

        def _select_dropdown_retry(label, text, attempts=3):
            last_msg = ""
            for attempt in range(max(int(attempts), 1)):
                ok_select, msg_select = bridge.select_dropdown(label, text)
                if ok_select:
                    return True, msg_select
                last_msg = str(msg_select)
                if is_cdp_target_closed_error(last_msg):
                    return False, last_msg
                if attempt < attempts - 1:
                    time.sleep(0.35 + attempt * 0.35)
            return False, last_msg

        def _extract_form_value(snapshot, field_name, use_raw=False):
            fields = (snapshot or {}).get("fields", {}) or {}
            field_info = fields.get(field_name, {}) or {}
            key = "raw" if use_raw else "value"
            return str(field_info.get(key, "") or "").strip()

        def _extract_popup_khdh_values(snapshot):
            popup_ppct_raw = _extract_form_value(snapshot, "tiet_ppct", use_raw=True) or _extract_form_value(snapshot, "tiet_ppct")
            popup_noi_dung = _extract_form_value(snapshot, "noi_dung", use_raw=True) or _extract_form_value(snapshot, "noi_dung")
            return {
                "thu": _digits(_extract_form_value(snapshot, "thu")),
                "tiet": _digits(_extract_form_value(snapshot, "tiet")),
                "ngay": _normalize_date_token(
                    _extract_form_value(snapshot, "ngay", use_raw=True)
                    or _extract_form_value(snapshot, "ngay")
                ),
                "mon_hoc_id": _extract_form_value(snapshot, "mon_hoc_id"),
                "mon_hoc_text": _extract_form_value(snapshot, "mon_hoc_id", use_raw=True),
                "phan_mon_id": _extract_form_value(snapshot, "phan_mon_id"),
                "phan_mon_text": _extract_form_value(snapshot, "phan_mon_id", use_raw=True),
                "ppct": _digits(popup_ppct_raw),
                "ppct_raw": str(popup_ppct_raw or "").strip(),
                "noi_dung": str(popup_noi_dung or "").strip(),
            }

        def _verify_snapshot_matches_row(snapshot, row_info):
            hard_issues = []
            soft_issues = []
            popup = _extract_popup_khdh_values(snapshot)

            row_thu = _digits(row_info.get("thu"))
            row_tiet = _digits(row_info.get("tiet"))
            row_ngay = _normalize_date_token(row_info.get("ngay"))
            row_mon_hoc_id = str(row_info.get("mon_hoc_id", "") or "").strip()
            row_phan_mon_id = str(row_info.get("phan_mon_id", "") or "").strip()
            row_ppct = _digits(row_info.get("ppct_hint"))
            row_noi_dung = str(row_info.get("noi_dung_hint", "") or "").strip()

            if row_thu and popup["thu"] and row_thu != popup["thu"]:
                hard_issues.append(f"thu mismatch {popup['thu']} != {row_thu}")
            if row_tiet and popup["tiet"] and row_tiet != popup["tiet"]:
                hard_issues.append(f"tiet mismatch {popup['tiet']} != {row_tiet}")
            if row_ngay and popup["ngay"] and row_ngay != popup["ngay"]:
                hard_issues.append(f"ngay mismatch {popup['ngay']} != {row_ngay}")

            if row_mon_hoc_id and popup["mon_hoc_id"] and row_mon_hoc_id != popup["mon_hoc_id"]:
                soft_issues.append(f"mon_hoc_id mismatch {popup['mon_hoc_id']} != {row_mon_hoc_id}")
            if row_phan_mon_id and popup["phan_mon_id"] and row_phan_mon_id != popup["phan_mon_id"]:
                soft_issues.append(f"phan_mon_id mismatch {popup['phan_mon_id']} != {row_phan_mon_id}")
            if row_ppct and popup["ppct"] and row_ppct != popup["ppct"]:
                soft_issues.append(f"PPCT mismatch {popup['ppct']} != {row_ppct}")
            if row_noi_dung and popup["noi_dung"]:
                expected = _normalize_text(row_noi_dung)
                actual = _normalize_text(popup["noi_dung"])
                if expected and actual and expected not in actual and actual not in expected:
                    soft_issues.append("noi_dung mismatch")

            return len(hard_issues) == 0, hard_issues, soft_issues, popup

        discovered_total = completed + skipped + errors

        try:
            bridge = ChromeBridge(port=params["port"])
            ok, msg = bridge.connect()
            if not ok:
                q.put(("error", f"Kết nối CDP thất bại: {msg}"))
                errors += 1
            else:
                bridge.setup_dialog_auto_accept()
                q.put(("log", "CDP connected (KHDH worker)", "success"))

                week_lop_cache = {}
                work_items = []
                for lop_idx in range(max(start_lop_idx, 0), len(lop_list)):
                    lop_text = lop_list[lop_idx]
                    first_week = start_tuan_num if lop_idx == start_lop_idx else tuan_from
                    for tuan_num in range(first_week, tuan_to + 1):
                        work_items.append((lop_idx, lop_text, tuan_num))

                for lop_idx, lop_text, tuan_num in work_items:
                    is_resume_anchor = (
                        lop_idx == start_lop_idx and tuan_num == start_tuan_num
                    )
                    row_begin_idx = start_slot_idx if is_resume_anchor else 0
                    if self._schedule_stop_event.is_set():
                        stopped = True
                        checkpoint_state = _build_checkpoint(
                            lop_idx,
                            tuan_num,
                            row_begin_idx,
                            start_row_key if is_resume_anchor else None,
                        )
                        break

                    tuan_text = f"Tuần {tuan_num}"
                    q.put(("log", f"📅 Chuyển đến {tuan_text}...", "info"))
                    ok, msg = _select_dropdown_retry("tuan", tuan_text, attempts=3)
                    if not ok:
                        if is_cdp_target_closed_error(msg):
                            _mark_cdp_closed_stop(lop_idx, tuan_num, row_begin_idx, msg)
                            break
                        errors += 1
                        checkpoint_state = _build_checkpoint(
                            lop_idx,
                            tuan_num,
                            row_begin_idx,
                            start_row_key if is_resume_anchor else None,
                        )
                        _emit_system_result("error_select_tuan", lop_text, tuan_num, f"Lỗi chọn tuần: {msg}")
                        q.put(("log", f"❌ Lỗi chọn {tuan_text}: {msg}", "error"))
                        q.put(("checkpoint", checkpoint_state))
                        continue

                    available_lop_keys = week_lop_cache.get(tuan_num)
                    if available_lop_keys is None:
                        ok_lops, lops_or_error = bridge.get_lop_options()
                        if not ok_lops:
                            if is_cdp_target_closed_error(lops_or_error):
                                _mark_cdp_closed_stop(lop_idx, tuan_num, row_begin_idx, lops_or_error)
                                break
                            q.put((
                                "log",
                                f"⚠ {tuan_text}: Không đọc được danh sách lớp để lọc trước: {lops_or_error}. "
                                "App sẽ thử chọn lớp trực tiếp.",
                                "warning",
                            ))
                            available_lop_keys = None
                        else:
                            available_lop_keys = {
                                str(item or "").strip().casefold()
                                for item in list(lops_or_error or [])
                                if str(item or "").strip()
                            }
                            week_lop_cache[tuan_num] = available_lop_keys

                    if available_lop_keys is not None and lop_text.casefold() not in available_lop_keys:
                        skipped += 1
                        checkpoint_state = _next_checkpoint_after_week(lop_idx, tuan_num)
                        _emit_system_result(
                            "skipped_unavailable_class",
                            lop_text,
                            tuan_num,
                            "Lớp này không xuất hiện trong dropdown của tuần hiện tại, bỏ qua hợp lệ.",
                        )
                        q.put((
                            "log",
                            f"⏭ Lớp {lop_text} | {tuan_text}: lớp không có trong tuần này, bỏ qua.",
                            "info",
                        ))
                        q.put(("checkpoint", checkpoint_state))
                        continue

                    q.put(("log", f"🏫 Chọn Lớp {lop_text}...", "info"))
                    ok, msg = _select_dropdown_retry("lop", lop_text, attempts=3)
                    if not ok:
                        if is_cdp_target_closed_error(msg):
                            _mark_cdp_closed_stop(lop_idx, tuan_num, row_begin_idx, msg)
                            break
                        errors += 1
                        checkpoint_state = _build_checkpoint(
                            lop_idx,
                            tuan_num,
                            row_begin_idx,
                            start_row_key if is_resume_anchor else None,
                        )
                        _emit_system_result("error_select_lop", lop_text, tuan_num, f"Lỗi chọn lớp {lop_text}: {msg}")
                        q.put(("log", f"❌ Lỗi chọn Lớp {lop_text}: {msg}", "error"))
                        q.put(("checkpoint", checkpoint_state))
                        continue

                    ok, msg = bridge.set_goi_y_khdh_mode(True)
                    if not ok:
                        if is_cdp_target_closed_error(msg):
                            _mark_cdp_closed_stop(lop_idx, tuan_num, row_begin_idx, msg)
                            break
                        errors += 1
                        checkpoint_state = _build_checkpoint(
                            lop_idx,
                            tuan_num,
                            row_begin_idx,
                            start_row_key if is_resume_anchor else None,
                        )
                        _emit_system_result("error_set_khdh_mode", lop_text, tuan_num, msg)
                        q.put(("log", f"❌ {tuan_text}: {msg}", "error"))
                        q.put(("checkpoint", checkpoint_state))
                        continue

                    ok_rows, khdh_rows = bridge.read_khdh_suggested_rows()
                    if not ok_rows:
                        if is_cdp_target_closed_error(khdh_rows):
                            _mark_cdp_closed_stop(lop_idx, tuan_num, row_begin_idx, khdh_rows)
                            break
                        errors += 1
                        checkpoint_state = _build_checkpoint(
                            lop_idx,
                            tuan_num,
                            row_begin_idx,
                            start_row_key if is_resume_anchor else None,
                        )
                        _emit_system_result("error_read_khdh_rows", lop_text, tuan_num, str(khdh_rows))
                        q.put(("log", f"❌ {tuan_text}: Không đọc được row đỏ KHDH: {khdh_rows}", "error"))
                        q.put(("checkpoint", checkpoint_state))
                        continue

                    khdh_rows = [
                        row for row in (khdh_rows or [])
                        if row.get("has_add_btn") and row.get("rowIdx") is not None
                    ]
                    if is_resume_anchor and start_row_key:
                        matched_idx = next(
                            (
                                idx for idx, row in enumerate(khdh_rows)
                                if _row_resume_key(row) == start_row_key
                            ),
                            None,
                        )
                        if matched_idx is not None:
                            row_begin_idx = matched_idx
                        else:
                            q.put((
                                "log",
                                f"⚠ {tuan_text}: Không còn thấy row checkpoint key={start_row_key}. "
                                "App sẽ quét lại từ row đỏ đầu tiên hiện còn để tránh skip nhầm.",
                                "warning",
                            ))
                            row_begin_idx = 0
                    discovered_total += len(khdh_rows)
                    q.put(("progress_total", max(discovered_total, 1)))
                    if not khdh_rows:
                        q.put(("log", f"ℹ Lớp {lop_text} | {tuan_text}: Không có row đỏ KHDH nào cần nhập.", "info"))
                        checkpoint_state = _next_checkpoint_after_week(lop_idx, tuan_num)
                        q.put(("checkpoint", checkpoint_state))
                        continue

                    if row_begin_idx >= len(khdh_rows):
                        q.put(("log", f"ℹ Lớp {lop_text} | {tuan_text}: Không còn row KHDH nào để tiếp tục ở checkpoint hiện tại.", "info"))
                        checkpoint_state = _next_checkpoint_after_week(lop_idx, tuan_num)
                        q.put(("checkpoint", checkpoint_state))
                        continue

                    q.put(("log",
                           f"📊 Lớp {lop_text} | {tuan_text}: phát hiện {len(khdh_rows)} row đỏ KHDH cần xử lý.",
                           "info"))

                    for red_idx in range(row_begin_idx, len(khdh_rows)):
                        if self._schedule_stop_event.is_set():
                            stopped = True
                            next_row = khdh_rows[red_idx] if red_idx < len(khdh_rows) else None
                            checkpoint_state = _build_checkpoint(
                                lop_idx,
                                tuan_num,
                                red_idx,
                                _row_resume_key(next_row) if next_row else None,
                            )
                            break

                        row_info = khdh_rows[red_idx]
                        slot_label = self._format_schedule_slot_label(
                            row_info.get("thu", "?"),
                            row_info.get("buoi", "?"),
                            row_info.get("tiet", "?"),
                        )
                        row_ppct_hint = _digits(row_info.get("ppct_hint"))
                        checkpoint_state = _build_checkpoint(
                            lop_idx,
                            tuan_num,
                            red_idx,
                            _row_resume_key(row_info),
                        )
                        q.put(("checkpoint", checkpoint_state))
                        q.put((
                            "progress",
                            f"Lớp {lop_text} | {tuan_text} | {slot_label} | KHDH PPCT {row_ppct_hint or '--'}",
                            completed + skipped + errors,
                        ))

                        if not row_info.get("has_add_btn"):
                            skipped += 1
                            _emit_slot_result(
                                "skipped_existing",
                                lop_text,
                                tuan_num,
                                row_info,
                                "Row KHDH không còn nút + (có thể đã nhập trước đó)",
                                row_ppct_hint or None,
                            )
                            q.put(("log", f"⏭ {slot_label}: Không còn nút +, bỏ qua.", "info"))
                            continue

                        ok, msg = bridge.click_add_button(
                            row_index=int(row_info.get("add_btn_index", -1) or -1),
                            row_dom_index=int(row_info.get("rowIdx", -1) or -1),
                        )
                        if not ok:
                            errors += 1
                            _emit_slot_result(
                                "error_click_add",
                                lop_text,
                                tuan_num,
                                row_info,
                                f"Click + thất bại: {msg}",
                                row_ppct_hint or None,
                            )
                            q.put(("log", f"❌ {slot_label}: Click + thất bại: {msg}", "error"))
                            continue

                        ok_form, msg_form = bridge.wait_for_lesson_form(
                            timeout_s=4.0,
                            poll_interval=0.12,
                        )
                        if not ok_form:
                            errors += 1
                            _emit_slot_result(
                                "error_open_form",
                                lop_text,
                                tuan_num,
                                row_info,
                                f"Form chưa mở sẵn sàng: {msg_form}",
                                row_ppct_hint or None,
                            )
                            q.put(("log", f"❌ {slot_label}: Form chưa mở sẵn sàng: {msg_form}", "error"))
                            try:
                                bridge.close_form()
                                bridge.wait_for_lesson_form_closed(timeout_s=1.0, poll_interval=0.06)
                            except Exception:
                                pass
                            continue

                        ok_snapshot, snapshot = bridge.get_open_lesson_form_snapshot()
                        if not ok_snapshot:
                            errors += 1
                            stopped = True
                            if not self._schedule_stop_reason:
                                self._schedule_stop_reason = "không đọc được popup KHDH để verify"
                            _emit_slot_result(
                                "error_verify_popup",
                                lop_text,
                                tuan_num,
                                row_info,
                                str(snapshot),
                                row_ppct_hint or None,
                            )
                            q.put(("log", f"❌ {slot_label}: Không đọc được popup để verify: {snapshot}", "error"))
                            try:
                                bridge.close_form()
                                bridge.wait_for_lesson_form_closed(timeout_s=1.0, poll_interval=0.06)
                            except Exception:
                                pass
                            break

                        matched, issues, soft_issues, popup_values = _verify_snapshot_matches_row(snapshot, row_info)
                        popup_ppct = popup_values.get("ppct", "")
                        if not matched:
                            errors += 1
                            stopped = True
                            if not self._schedule_stop_reason:
                                self._schedule_stop_reason = "popup mở sai row KHDH"
                            error_message = "Popup không khớp row KHDH: " + "; ".join(issues)
                            _emit_slot_result(
                                "error_verify_popup",
                                lop_text,
                                tuan_num,
                                row_info,
                                error_message,
                                popup_ppct or row_ppct_hint or None,
                            )
                            q.put(("log", f"❌ {slot_label}: {error_message} | Dừng để tránh nhập nhầm row", "error"))
                            try:
                                bridge.close_form()
                                bridge.wait_for_lesson_form_closed(timeout_s=1.0, poll_interval=0.06)
                            except Exception:
                                pass
                            break

                        if soft_issues:
                            q.put((
                                "log",
                                f"⚠ {slot_label}: hint row đỏ lệch popup KHDH ({'; '.join(soft_issues)}). "
                                "App sẽ ưu tiên dữ liệu popup đang mở để tránh nhập sai.",
                                "warning",
                            ))

                        target_mon_hoc_id = str(
                            popup_values.get("mon_hoc_id")
                            or row_info.get("mon_hoc_id", "")
                            or ""
                        ).strip()
                        target_phan_mon_id = str(
                            popup_values.get("phan_mon_id")
                            or row_info.get("phan_mon_id", "")
                            or ""
                        ).strip()
                        target_ppct = str(
                            popup_values.get("ppct_raw")
                            or popup_values.get("ppct")
                            or row_info.get("ppct_hint", "")
                            or ""
                        ).strip()
                        target_noi_dung = str(
                            popup_values.get("noi_dung")
                            or row_info.get("noi_dung_hint", "")
                            or ""
                        ).strip()
                        target_mon_hoc_text = str(
                            popup_values.get("mon_hoc_text")
                            or row_info.get("mon_hoc_text_hint")
                            or row_info.get("mon_hoc_hint")
                            or ""
                        ).strip()
                        target_phan_mon_text = str(
                            popup_values.get("phan_mon_text")
                            or row_info.get("phan_mon_text_hint", "")
                            or ""
                        ).strip()

                        missing_fields = []
                        if not target_ppct:
                            missing_fields.append("PPCT")
                        if not target_noi_dung:
                            missing_fields.append("nội dung")
                        if missing_fields:
                            errors += 1
                            error_message = (
                                "Row KHDH thiếu dữ liệu để fill popup: "
                                + ", ".join(missing_fields)
                            )
                            _emit_slot_result(
                                "error_khdh_missing_data",
                                lop_text,
                                tuan_num,
                                row_info,
                                error_message,
                                popup_ppct or row_ppct_hint or None,
                            )
                            q.put(("log", f"❌ {slot_label}: {error_message}", "error"))
                            try:
                                bridge.close_form()
                                bridge.wait_for_lesson_form_closed(timeout_s=1.0, poll_interval=0.06)
                            except Exception:
                                pass
                            continue

                        nhan_xet = random.choice(nhan_xet_items)
                        popup_has_khdh_payload = bool(
                            str(
                                popup_values.get("ppct_raw")
                                or popup_values.get("ppct")
                                or ""
                            ).strip()
                            and str(popup_values.get("noi_dung") or "").strip()
                        )
                        if popup_has_khdh_payload:
                            q.put(("log",
                                   f"📝 {slot_label}: giữ dữ liệu popup KHDH | MH={target_mon_hoc_text or target_mon_hoc_id or '--'}, PPCT={target_ppct}, NX={nhan_xet[:30]}...",
                                   "info"))
                            ok_fill, msg_fill = bridge.fill_form_minimal(
                                hs_nghi=hs_nghi,
                                nhan_xet=nhan_xet,
                                diem=diem,
                            )
                            q.put(("log", f"   📋 Fill KHDH tối thiểu: {msg_fill}", "info"))
                        else:
                            q.put(("log",
                                   f"📝 {slot_label}: fallback fill KHDH từ hint row | MH={target_mon_hoc_id or '--'}, PM={target_phan_mon_id or '--'}, PPCT={target_ppct}, NX={nhan_xet[:30]}...",
                                   "info"))
                            ok_fill, msg_fill = bridge.fill_form(
                                ppct=target_ppct,
                                hs_nghi=hs_nghi,
                                nhan_xet=nhan_xet,
                                diem=diem,
                                phan_mon_index=target_phan_mon_id or None,
                                mon_hoc_index=target_mon_hoc_id or None,
                                phan_mon_text=target_phan_mon_text or None,
                                mon_hoc_text=target_mon_hoc_text or None,
                                mon_hoc_field="mon_hoc_id",
                                noi_dung=target_noi_dung,
                            )
                            q.put(("log", f"   📋 Fill KHDH fallback: {msg_fill}", "info"))
                        if not ok_fill:
                            errors += 1
                            _emit_slot_result(
                                "error_fill",
                                lop_text,
                                tuan_num,
                                row_info,
                                f"Fill KHDH lỗi: {msg_fill}",
                                popup_ppct or row_ppct_hint or None,
                            )
                            q.put(("log", f"❌ {slot_label}: Fill KHDH lỗi: {msg_fill}", "error"))
                            try:
                                bridge.close_form()
                                bridge.wait_for_lesson_form_closed(timeout_s=1.0, poll_interval=0.06)
                            except Exception:
                                pass
                            continue

                        bridge.wait_for_form_ready_to_save(timeout_s=0.8, poll_interval=0.06)
                        ok_save, msg_save, save_meta = bridge.save_form_auto(
                            buoi_hoc=row_info.get("buoi", ""),
                        )
                        if save_meta.get("fallback_used"):
                            q.put((
                                "log",
                                "   ↩ API save không dùng được, đã fallback sang click-save",
                                "warning",
                            ))

                        ok_commit = False
                        commit_msg = ""
                        _saved_row = None
                        if ok_save:
                            ok_commit = True
                            commit_msg = msg_save
                        else:
                            ok_commit, commit_msg, _saved_row = bridge.wait_for_slot_data_fetch(
                                lop_text,
                                tuan_num,
                                row_info.get("thu", ""),
                                row_info.get("buoi", ""),
                                row_info.get("tiet", ""),
                                timeout_s=4.5,
                                poll_interval=0.3,
                            )
                            if not ok_commit:
                                ok_commit, commit_msg, _saved_row = bridge.wait_for_slot_data(
                                    row_info.get("thu", ""),
                                    row_info.get("buoi", ""),
                                    row_info.get("tiet", ""),
                                    timeout_s=3.0,
                                    poll_interval=0.25,
                                )

                        if ok_commit:
                            completed += 1
                            popup_ppct_int = None
                            try:
                                popup_ppct_int = int(str(target_ppct or popup_ppct or row_ppct_hint or "").strip())
                            except Exception:
                                popup_ppct_int = None
                            if popup_ppct_int is not None:
                                last_success_ppct = popup_ppct_int
                                q.put(("ppct_sync", last_success_ppct, None))
                            if not ok_save:
                                q.put(("log",
                                       f"⚠ {slot_label}: Save báo lỗi nhưng bảng đã cập nhật ({commit_msg})",
                                       "warning"))
                                try:
                                    bridge.close_form()
                                    bridge.wait_for_lesson_form_closed(timeout_s=1.0, poll_interval=0.06)
                                except Exception:
                                    pass
                            _emit_slot_result(
                                "success",
                                lop_text,
                                tuan_num,
                                row_info,
                                "Đã lưu thành công",
                                target_ppct or popup_ppct or row_ppct_hint or None,
                            )
                            q.put(("log",
                                   f"✅ {slot_label} | KHBD/KHDH PPCT={target_ppct or popup_ppct or row_ppct_hint or '--'}: OK",
                                   "success"))
                        else:
                            errors += 1
                            error_message = (
                                f"Save lỗi: {msg_save}"
                                + (f" | Verify: {commit_msg}" if commit_msg else "")
                            )
                            request_sent = bool(save_meta.get("request_sent", False))
                            if request_sent:
                                stopped = True
                                if not self._schedule_stop_reason:
                                    self._schedule_stop_reason = "save KHBD/KHDH mơ hồ cần xác minh"
                                _emit_slot_result(
                                    "error_save_ambiguous",
                                    lop_text,
                                    tuan_num,
                                    row_info,
                                    error_message,
                                    popup_ppct or row_ppct_hint or None,
                                )
                                q.put(("log",
                                       f"❌ {slot_label}: {error_message} | Dừng để tránh lệch checkpoint",
                                       "error"))
                            else:
                                _emit_slot_result(
                                    "error_save",
                                    lop_text,
                                    tuan_num,
                                    row_info,
                                    error_message,
                                    popup_ppct or row_ppct_hint or None,
                                )
                                q.put(("log", f"❌ {slot_label}: {error_message}", "error"))
                            try:
                                bridge.close_form()
                                bridge.wait_for_lesson_form_closed(timeout_s=1.2, poll_interval=0.06)
                            except Exception:
                                pass

                        if stopped:
                            break

                        next_row_idx = red_idx + 1
                        if next_row_idx < len(khdh_rows):
                            checkpoint_state = _build_checkpoint(lop_idx, tuan_num, next_row_idx)
                        else:
                            checkpoint_state = _next_checkpoint_after_week(lop_idx, tuan_num)
                        q.put(("checkpoint", checkpoint_state))
                        q.put((
                            "progress",
                            f"Lớp {lop_text} | {tuan_text} | {slot_label}",
                            completed + skipped + errors,
                        ))

                    q.put(("log",
                           f"📅 Lớp {lop_text} | {tuan_text} xong (KHDH): "
                           f"{completed} nhập, {skipped} skip, {errors} lỗi",
                           "info"))
                    if stopped:
                        break

        except Exception as e:
            errors += 1
            stopped = True
            q.put(("error",
                   f"Schedule worker KHDH exception: {type(e).__name__}: "
                   f"{str(e)[:140]}"))
        finally:
            if bridge:
                try:
                    ok_cleanup, msg_cleanup = bridge.cleanup_after_automation(restore_view_mode=True)
                    q.put((
                        "log",
                        f"Cleanup sau KHDH: {msg_cleanup}" if ok_cleanup else f"Cleanup sau KHDH lỗi: {msg_cleanup}",
                        "info" if ok_cleanup else "warning",
                    ))
                except Exception:
                    pass
                try:
                    bridge.disconnect()
                except Exception:
                    pass
            has_remaining = (
                int(checkpoint_state.get("next_lop_idx", len(lop_list)) or 0) < len(lop_list)
                if checkpoint_state else False
            )
            resume_payload = copy.deepcopy(checkpoint_state) if (stopped and has_remaining) else None
            q.put(("done", {
                "completed": completed,
                "skipped": skipped,
                "errors": errors,
                "stopped": bool(stopped and has_remaining),
                "stop_reason": self._schedule_stop_reason,
                "resume_state": resume_payload,
                "last_success_ppct": last_success_ppct,
                "next_ppct": None,
            }))

    def _schedule_worker(self, params):
        """Worker thread schedule với checkpoint resume và báo cáo chi tiết từng slot."""
        bridge = None
        q = self._schedule_queue
        slots = params["slots"]
        tuan_from = int(params["tuan_from"])
        tuan_to = int(params["tuan_to"])
        lop_text = params["lop"]
        resume_state = params.get("resume_state") or {}
        completed = int(resume_state.get("completed", 0) or 0)
        skipped = int(resume_state.get("skipped", 0) or 0)
        errors = int(resume_state.get("errors", 0) or 0)
        ppct_counter = int(
            resume_state.get("next_ppct", params["ppct_start"]) or params["ppct_start"]
        )
        last_success_ppct = resume_state.get("last_success_ppct")
        start_tuan_num = int(resume_state.get("next_tuan_num", tuan_from) or tuan_from)
        start_slot_idx = int(resume_state.get("next_slot_idx", 0) or 0)
        stopped = False
        checkpoint_state = {
            "next_tuan_num": start_tuan_num,
            "next_slot_idx": start_slot_idx,
            "next_ppct": ppct_counter,
            "completed": completed,
            "skipped": skipped,
            "errors": errors,
            "last_success_ppct": last_success_ppct,
        }

        hs_nghi = params["hs_nghi"]
        diem = params["diem"]
        nhan_xet_raw = params["nhan_xet_raw"]
        phan_mon_value = params.get("phan_mon_value")
        mon_hoc_value = params.get("mon_hoc_value")
        phan_mon_text = params.get("phan_mon_text")
        mon_hoc_text = params.get("mon_hoc_text")
        mon_hoc_field = params.get("mon_hoc_field")

        nhan_xet_items = [x.strip() for x in nhan_xet_raw.split("|") if x.strip()]
        if not nhan_xet_items:
            nhan_xet_items = ["Lớp học chăm ngoan"]

        def _build_checkpoint(next_tuan_num, next_slot_idx):
            return {
                "next_tuan_num": int(next_tuan_num),
                "next_slot_idx": int(next_slot_idx),
                "next_ppct": int(ppct_counter),
                "completed": int(completed),
                "skipped": int(skipped),
                "errors": int(errors),
                "last_success_ppct": last_success_ppct,
            }

        def _next_position(tuan_num, slot_idx):
            if slot_idx + 1 < len(slots):
                return tuan_num, slot_idx + 1
            return tuan_num + 1, 0

        def _emit_slot_result(status, tuan_num, slot, message, ppct_value=None):
            q.put(("slot_result", {
                "status": status,
                "week_num": int(tuan_num),
                "week_text": f"Tuần {tuan_num}",
                "slot_label": self._format_schedule_slot_label(
                    slot.get("thu", "?"),
                    slot.get("buoi", "?"),
                    slot.get("tiet", "?"),
                ),
                "ppct": ppct_value,
                "message": message,
            }))

        def _emit_system_result(status, tuan_num, message):
            q.put(("slot_result", {
                "status": status,
                "week_num": int(tuan_num),
                "week_text": f"Tuần {tuan_num}",
                "slot_label": "Khởi tạo tuần/lớp",
                "ppct": None,
                "message": message,
            }))

        week_table_rows = []
        week_row_lookup = {}

        def _slot_key(thu, buoi, tiet):
            # Chuẩn hoá thu/buoi về token thống nhất (CN/8, Sáng/Chiều có dấu)
            # để slot từ UI khớp đúng row từ read_table, kể cả Chủ nhật.
            return (
                ChromeBridge._normalize_thu_token(thu),
                ChromeBridge._normalize_buoi_token(buoi),
                str(tiet).strip(),
            )

        def _set_week_snapshot(rows):
            nonlocal week_table_rows, week_row_lookup
            week_table_rows = list(rows or [])
            week_row_lookup = {}
            for row in week_table_rows:
                key = _slot_key(row.get("thu", ""), row.get("buoi", ""), row.get("tiet", ""))
                week_row_lookup[key] = row

        def _refresh_week_snapshot():
            ok_table, table_data = bridge.read_table()
            if ok_table:
                _set_week_snapshot(table_data)
            return ok_table, table_data

        def _get_cached_row(slot, refresh_if_missing=True):
            key = _slot_key(slot.get("thu", ""), slot.get("buoi", ""), slot.get("tiet", ""))
            matched = week_row_lookup.get(key)
            if matched is None and refresh_if_missing:
                ok_table, table_data = _refresh_week_snapshot()
                if not ok_table:
                    return None, f"Không đọc được bảng: {table_data}"
                matched = week_row_lookup.get(key)
            return matched, None

        def _mark_cached_slot_saved(slot, ppct_value, saved_row=None):
            key = _slot_key(slot.get("thu", ""), slot.get("buoi", ""), slot.get("tiet", ""))
            if saved_row:
                week_row_lookup[key] = saved_row
                return
            matched = week_row_lookup.get(key)
            if matched is None:
                return
            matched["has_data"] = True
            matched["ppct"] = str(ppct_value)
            if mon_hoc_text and phan_mon_text:
                matched["mon_hoc"] = f"{mon_hoc_text} ({phan_mon_text})"
            elif mon_hoc_text:
                matched["mon_hoc"] = str(mon_hoc_text)

        def _mark_cdp_closed_stop(tuan_num, slot_idx, message):
            nonlocal errors, stopped, checkpoint_state
            errors += 1
            stopped = True
            checkpoint_state = _build_checkpoint(tuan_num, slot_idx)
            if not self._schedule_stop_reason:
                self._schedule_stop_reason = "CDP/Chrome đã đóng hoặc tab VnEdu không còn khả dụng"
            q.put(("checkpoint", checkpoint_state))
            q.put(("error", (
                "CDP/Chrome đã đóng hoặc tab VnEdu không còn khả dụng. "
                f"Worker schedule dừng tại Tuần {tuan_num}. Chi tiết: {message}"
            )))

        def _select_dropdown_retry(label, text, attempts=3):
            last_msg = ""
            for attempt in range(max(int(attempts), 1)):
                ok_select, msg_select = bridge.select_dropdown(label, text)
                if ok_select:
                    return True, msg_select
                last_msg = str(msg_select)
                if is_cdp_target_closed_error(last_msg):
                    return False, last_msg
                if attempt < attempts - 1:
                    time.sleep(0.35 + attempt * 0.35)
            return False, last_msg

        try:
            bridge = ChromeBridge(port=params["port"])
            ok, msg = bridge.connect()
            if not ok:
                q.put(("error", f"Kết nối CDP thất bại: {msg}"))
                errors += 1
            else:
                bridge.setup_dialog_auto_accept()
                q.put(("log", "CDP connected (schedule worker)", "success"))

                for tuan_num in range(start_tuan_num, tuan_to + 1):
                    slot_begin_idx = start_slot_idx if tuan_num == start_tuan_num else 0
                    if self._schedule_stop_event.is_set():
                        stopped = True
                        checkpoint_state = _build_checkpoint(tuan_num, slot_begin_idx)
                        break

                    tuan_text = f"Tuần {tuan_num}"
                    q.put(("log", f"📅 Chuyển đến {tuan_text}...", "info"))
                    ok, msg = _select_dropdown_retry("tuan", tuan_text, attempts=3)
                    if not ok:
                        if is_cdp_target_closed_error(msg):
                            _mark_cdp_closed_stop(tuan_num, slot_begin_idx, msg)
                            break
                        errors += 1
                        checkpoint_state = _build_checkpoint(tuan_num, slot_begin_idx)
                        _emit_system_result("error_select_tuan", tuan_num, f"Lỗi chọn tuần: {msg}")
                        q.put(("log", f"❌ Lỗi chọn {tuan_text}: {msg}", "error"))
                        q.put(("checkpoint", checkpoint_state))
                        continue

                    q.put(("log", f"🏫 Chọn Lớp {lop_text}...", "info"))
                    ok, msg = _select_dropdown_retry("lop", lop_text, attempts=3)
                    if not ok:
                        if is_cdp_target_closed_error(msg):
                            _mark_cdp_closed_stop(tuan_num, slot_begin_idx, msg)
                            break
                        errors += 1
                        checkpoint_state = _build_checkpoint(tuan_num, slot_begin_idx)
                        _emit_system_result("error_select_lop", tuan_num, f"Lỗi chọn lớp {lop_text}: {msg}")
                        q.put(("log", f"❌ Lỗi chọn Lớp {lop_text}: {msg}", "error"))
                        q.put(("checkpoint", checkpoint_state))
                        continue

                    ok, table_data = _refresh_week_snapshot()
                    if not ok:
                        if is_cdp_target_closed_error(table_data):
                            _mark_cdp_closed_stop(tuan_num, slot_begin_idx, table_data)
                            break
                        errors += 1
                        checkpoint_state = _build_checkpoint(tuan_num, slot_begin_idx)
                        _emit_system_result(
                            "error_read_week_table",
                            tuan_num,
                            f"Lỗi đọc bảng tuần: {table_data}",
                        )
                        q.put(("log", f"❌ {tuan_text}: Lỗi đọc bảng tuần: {table_data}", "error"))
                        q.put(("checkpoint", checkpoint_state))
                        continue

                    q.put(("log",
                           f"📊 Bắt đầu quét {tuan_text} — {lop_text} "
                           f"({len(slots)} slots, {len(week_table_rows)} rows)...", "info"))

                    for slot_idx in range(slot_begin_idx, len(slots)):
                        if self._schedule_stop_event.is_set():
                            stopped = True
                            checkpoint_state = _build_checkpoint(tuan_num, slot_idx)
                            break

                        slot = slots[slot_idx]
                        s_thu = slot["thu"]
                        s_buoi = slot["buoi"]
                        s_tiet = slot["tiet"]
                        slot_label = self._format_schedule_slot_label(s_thu, s_buoi, s_tiet)
                        current_ppct = ppct_counter
                        checkpoint_state = _build_checkpoint(tuan_num, slot_idx)
                        q.put(("checkpoint", checkpoint_state))

                        matched_row, cache_error = _get_cached_row(slot, refresh_if_missing=True)
                        if cache_error:
                            errors += 1
                            _emit_slot_result(
                                "error_read_table",
                                tuan_num,
                                slot,
                                cache_error,
                                current_ppct,
                            )
                            q.put(("log", f"❌ {slot_label}: {cache_error}", "error"))
                        else:
                            if matched_row is None:
                                errors += 1
                                _emit_slot_result(
                                    "error_not_found",
                                    tuan_num,
                                    slot,
                                    "Không tìm thấy row trên bảng VnEdu",
                                    current_ppct,
                                )
                                q.put(("log", f"❌ {slot_label}: Không tìm thấy row", "error"))
                            else:
                                row_error_emitted = False
                                row_index = matched_row.get("add_btn_index", -1)
                                row_dom_index = matched_row.get("rowIdx")
                                has_data = bool(matched_row.get("has_data", False))
                                has_add_btn = bool(matched_row.get("has_add_btn", False))

                                if (
                                    not has_data and
                                    (
                                        not has_add_btn or
                                        (row_index < 0 and (row_dom_index is None or row_dom_index < 0))
                                    )
                                ):
                                    ok_refresh, refresh_data = _refresh_week_snapshot()
                                    if not ok_refresh:
                                        errors += 1
                                        _emit_slot_result(
                                            "error_read_table",
                                            tuan_num,
                                            slot,
                                            f"Không đọc được bảng sau refresh: {refresh_data}",
                                            current_ppct,
                                        )
                                        q.put((
                                            "log",
                                            f"❌ {slot_label}: Không đọc được bảng sau refresh: {refresh_data}",
                                            "error",
                                        ))
                                        row_error_emitted = True
                                        matched_row = None
                                    else:
                                        matched_row = week_row_lookup.get(
                                            _slot_key(s_thu, s_buoi, s_tiet)
                                        )
                                        if matched_row is not None:
                                            row_index = matched_row.get("add_btn_index", -1)
                                            row_dom_index = matched_row.get("rowIdx")
                                            has_data = bool(matched_row.get("has_data", False))
                                            has_add_btn = bool(matched_row.get("has_add_btn", False))

                                if matched_row is None:
                                    if not row_error_emitted:
                                        errors += 1
                                        _emit_slot_result(
                                            "error_not_found",
                                            tuan_num,
                                            slot,
                                            "Không tìm thấy row sau khi refresh bảng",
                                            current_ppct,
                                        )
                                        q.put((
                                            "log",
                                            f"❌ {slot_label}: Không tìm thấy row sau khi refresh bảng",
                                            "error",
                                        ))
                                elif has_data:
                                    existing_ppct_value = self._extract_existing_ppct_value(matched_row)
                                    if existing_ppct_value is None:
                                        errors += 1
                                        stopped = True
                                        if not self._schedule_stop_reason:
                                            self._schedule_stop_reason = (
                                                "row đã có dữ liệu nhưng không đọc được PPCT"
                                            )
                                        _emit_slot_result(
                                            "error_existing_ppct",
                                            tuan_num,
                                            slot,
                                            "Row đã có dữ liệu nhưng không đọc được PPCT hiện có",
                                            current_ppct,
                                        )
                                        q.put((
                                            "log",
                                            f"❌ {slot_label}: Row đã có dữ liệu nhưng không đọc được PPCT hiện có",
                                            "error",
                                        ))
                                    else:
                                        skipped += 1
                                        if existing_ppct_value >= current_ppct:
                                            last_success_ppct = existing_ppct_value
                                            ppct_counter = existing_ppct_value + 1
                                            q.put(("ppct_sync", last_success_ppct, ppct_counter))
                                        _emit_slot_result(
                                            "skipped_existing",
                                            tuan_num,
                                            slot,
                                            f"Đã có dữ liệu — PPCT hiện có {existing_ppct_value}",
                                            existing_ppct_value,
                                        )
                                        q.put((
                                            "log",
                                            f"⏭ {slot_label}: Đã có dữ liệu — PPCT hiện có {existing_ppct_value}",
                                            "info",
                                        ))
                                elif not has_add_btn:
                                    errors += 1
                                    _emit_slot_result(
                                        "error_no_add_button",
                                        tuan_num,
                                        slot,
                                        "Không có nút + để mở form",
                                        current_ppct,
                                    )
                                    q.put(("log", f"❌ {slot_label}: Không có nút +", "error"))
                                elif row_index < 0 and (row_dom_index is None or row_dom_index < 0):
                                    errors += 1
                                    _emit_slot_result(
                                        "error_row_index",
                                        tuan_num,
                                        slot,
                                        "Row không có add_btn_index/rowIdx hợp lệ",
                                        current_ppct,
                                    )
                                    q.put((
                                        "log",
                                        f"❌ {slot_label}: Row không có add_btn_index/rowIdx hợp lệ",
                                        "error",
                                    ))
                                else:
                                    q.put((
                                        "progress",
                                        f"{tuan_text} | {slot_label} | PPCT {current_ppct}",
                                        completed + skipped + errors,
                                    ))
                                    ok, msg = bridge.click_add_button(
                                        row_index=row_index,
                                        row_dom_index=row_dom_index,
                                    )
                                    if not ok:
                                        errors += 1
                                        _emit_slot_result(
                                            "error_click_add",
                                            tuan_num,
                                            slot,
                                            f"Click + thất bại: {msg}",
                                            current_ppct,
                                        )
                                        q.put(("log",
                                               f"❌ {slot_label}: Click + thất bại: {msg}",
                                               "error"))
                                    else:
                                        ok_form, msg_form = bridge.wait_for_lesson_form(
                                            timeout_s=4.0,
                                            poll_interval=0.12,
                                        )
                                        if not ok_form:
                                            errors += 1
                                            _emit_slot_result(
                                                "error_open_form",
                                                tuan_num,
                                                slot,
                                                f"Form chưa mở sẵn sàng: {msg_form}",
                                                current_ppct,
                                            )
                                            q.put((
                                                "log",
                                                f"❌ {slot_label}: Form chưa mở sẵn sàng: {msg_form}",
                                                "error",
                                            ))
                                            try:
                                                bridge.close_form()
                                                bridge.wait_for_lesson_form_closed(
                                                    timeout_s=1.0,
                                                    poll_interval=0.06,
                                                )
                                            except Exception:
                                                pass
                                            continue

                                        nhan_xet = random.choice(nhan_xet_items)
                                        q.put(("log",
                                               f"📝 {slot_label}: PPCT={current_ppct}, "
                                               f"MH={mon_hoc_value}, PM={phan_mon_value}, "
                                               f"NX={nhan_xet[:20]}...",
                                               "info"))

                                        ok_fill, msg_fill = bridge.fill_form(
                                            ppct=str(current_ppct),
                                            hs_nghi=hs_nghi,
                                            nhan_xet=nhan_xet,
                                            diem=diem,
                                            phan_mon_index=phan_mon_value,
                                            mon_hoc_index=mon_hoc_value,
                                            phan_mon_text=phan_mon_text,
                                            mon_hoc_text=mon_hoc_text,
                                            mon_hoc_field=mon_hoc_field,
                                            noi_dung=None,
                                        )
                                        q.put(("log", f"   📋 Fill: {msg_fill}", "info"))

                                        if not ok_fill:
                                            errors += 1
                                            _emit_slot_result(
                                                "error_fill",
                                                tuan_num,
                                                slot,
                                                f"Fill form lỗi: {msg_fill}",
                                                current_ppct,
                                            )
                                            q.put(("log",
                                                   f"❌ {slot_label}: Fill form lỗi: {msg_fill}",
                                                   "error"))
                                            try:
                                                bridge.close_form()
                                                bridge.wait_for_lesson_form_closed(
                                                    timeout_s=1.0,
                                                    poll_interval=0.06,
                                                )
                                            except Exception:
                                                pass
                                        else:
                                            bridge.wait_for_form_ready_to_save(
                                                timeout_s=0.8,
                                                poll_interval=0.06,
                                            )
                                            ok_save, msg_save, save_meta = bridge.save_form_auto(
                                                buoi_hoc=s_buoi,
                                            )
                                            if save_meta.get("fallback_used"):
                                                q.put((
                                                    "log",
                                                    "   ↩ API save không dùng được, đã fallback sang click-save",
                                                    "warning",
                                                ))
                                            ok_commit = False
                                            commit_msg = ""
                                            _saved_row = None
                                            if ok_save:
                                                ok_commit = True
                                                commit_msg = msg_save
                                            else:
                                                ok_commit, commit_msg, _saved_row = bridge.wait_for_slot_data_fetch(
                                                    lop_text,
                                                    tuan_num,
                                                    s_thu,
                                                    s_buoi,
                                                    s_tiet,
                                                    timeout_s=4.5,
                                                    poll_interval=0.3,
                                                )
                                                if not ok_commit:
                                                    ok_commit, commit_msg, _saved_row = bridge.wait_for_slot_data(
                                                        s_thu,
                                                        s_buoi,
                                                        s_tiet,
                                                        timeout_s=3.0,
                                                        poll_interval=0.25,
                                                    )

                                            if ok_commit:
                                                _mark_cached_slot_saved(
                                                    slot,
                                                    current_ppct,
                                                    saved_row=_saved_row,
                                                )
                                                if not ok_save:
                                                    q.put(("log",
                                                           f"⚠ {slot_label}: Save báo lỗi nhưng bảng đã cập nhật "
                                                           f"({commit_msg})",
                                                           "warning"))
                                                    try:
                                                        bridge.close_form()
                                                        bridge.wait_for_lesson_form_closed(
                                                            timeout_s=1.0,
                                                            poll_interval=0.06,
                                                        )
                                                    except Exception:
                                                        pass

                                                completed += 1
                                                last_success_ppct = current_ppct
                                                ppct_counter = current_ppct + 1
                                                q.put(("ppct_sync", last_success_ppct, ppct_counter))
                                                _emit_slot_result(
                                                    "success",
                                                    tuan_num,
                                                    slot,
                                                    "Đã lưu thành công",
                                                    current_ppct,
                                                )
                                                q.put(("log",
                                                       f"✅ {slot_label} PPCT={current_ppct}: OK",
                                                       "success"))
                                            else:
                                                errors += 1
                                                error_message = (
                                                    f"Save lỗi: {msg_save}"
                                                    + (f" | Verify: {commit_msg}" if commit_msg else "")
                                                )
                                                request_sent = bool(save_meta.get("request_sent", False))
                                                if request_sent:
                                                    stopped = True
                                                    if not self._schedule_stop_reason:
                                                        self._schedule_stop_reason = "save mơ hồ cần xác minh"
                                                    _emit_slot_result(
                                                        "error_save_ambiguous",
                                                        tuan_num,
                                                        slot,
                                                        error_message,
                                                        current_ppct,
                                                    )
                                                    q.put(("log",
                                                           f"❌ {slot_label}: {error_message} | Dừng để tránh lệch checkpoint",
                                                           "error"))
                                                else:
                                                    _emit_slot_result(
                                                        "error_save",
                                                        tuan_num,
                                                        slot,
                                                        error_message,
                                                        current_ppct,
                                                    )
                                                    q.put(("log",
                                                           f"❌ {slot_label}: {error_message}",
                                                           "error"))
                                                try:
                                                    bridge.close_form()
                                                    bridge.wait_for_lesson_form_closed(
                                                        timeout_s=1.2,
                                                        poll_interval=0.06,
                                                    )
                                                except Exception:
                                                    pass

                        if stopped:
                            break

                        next_tuan_num, next_slot_idx = _next_position(tuan_num, slot_idx)
                        checkpoint_state = _build_checkpoint(next_tuan_num, next_slot_idx)
                        q.put(("checkpoint", checkpoint_state))
                        q.put((
                            "progress",
                            f"{tuan_text} | {slot_label}",
                            completed + skipped + errors,
                        ))

                    q.put(("log",
                           f"📅 {tuan_text} xong: {completed} nhập, "
                           f"{skipped} skip, {errors} lỗi",
                           "info"))

        except Exception as e:
            errors += 1
            stopped = True
            q.put(("error",
                   f"Schedule worker exception: {type(e).__name__}: "
                   f"{str(e)[:140]}"))
        finally:
            if bridge:
                try:
                    ok_cleanup, msg_cleanup = bridge.cleanup_after_automation(restore_view_mode=False)
                    q.put((
                        "log",
                        f"Cleanup sau schedule: {msg_cleanup}" if ok_cleanup else f"Cleanup sau schedule lỗi: {msg_cleanup}",
                        "info" if ok_cleanup else "warning",
                    ))
                except Exception:
                    pass
                try:
                    bridge.disconnect()
                except Exception:
                    pass
            has_remaining = (
                checkpoint_state.get("next_tuan_num", tuan_to + 1) <= tuan_to
                if checkpoint_state else False
            )
            resume_payload = copy.deepcopy(checkpoint_state) if (stopped and has_remaining) else None
            q.put(("done", {
                "completed": completed,
                "skipped": skipped,
                "errors": errors,
                "stopped": bool(stopped and has_remaining),
                "stop_reason": self._schedule_stop_reason,
                "resume_state": resume_payload,
                "last_success_ppct": last_success_ppct,
                "next_ppct": resume_payload.get("next_ppct") if resume_payload else ppct_counter,
            }))

    def _poll_schedule_queue(self):
        """Poll schedule queue cho progress updates (chạy trong UI thread)."""
        try:
            while not self._schedule_queue.empty():
                msg = self._schedule_queue.get_nowait()
                msg_type = msg[0]

                if msg_type == "log":
                    self._log(msg[1], msg[2] if len(msg) > 2 else "info")

                elif msg_type == "progress":
                    text = msg[1]
                    count = msg[2] if len(msg) > 2 else 0
                    self.lbl_sched_progress.config(text=f"⏳ {text}")
                    current_total = int(float(self.sched_progressbar["maximum"] or 1))
                    self._set_sched_live_progress(
                        current=count,
                        total=current_total,
                        phase="Schedule",
                        detail=text,
                        state="running",
                    )

                elif msg_type == "progress_total":
                    try:
                        self.sched_progressbar["maximum"] = max(
                            int(msg[1]),
                            int(float(self.sched_progressbar["maximum"] or 1)),
                        )
                    except Exception:
                        pass

                elif msg_type == "ppct_sync":
                    self._update_sched_ppct_runtime(
                        last_success=msg[1],
                        next_ppct=msg[2] if len(msg) > 2 else None,
                        status="running",
                    )

                elif msg_type == "slot_result":
                    self._schedule_results.append(msg[1])

                elif msg_type == "checkpoint":
                    self._update_schedule_resume_snapshot(msg[1], persist=True)

                elif msg_type == "error":
                    self._log(msg[1], "error")

                elif msg_type == "done":
                    self._schedule_finished(msg[1])
                    return

        except Exception as e:
            print(f"[SCHED_POLL] Error: {e}")

        if self._schedule_running and self._root_exists():
            self.root.after(150, self._poll_schedule_queue)

    def _schedule_finished(self, summary):
        """Xử lý khi schedule hoàn tất hoặc tạm dừng."""
        completed = int(summary.get("completed", 0) or 0)
        skipped_count = int(summary.get("skipped", 0) or 0)
        error_count = int(summary.get("errors", 0) or 0)
        resume_state = summary.get("resume_state")
        stopped = bool(summary.get("stopped"))
        next_ppct = summary.get("next_ppct")
        last_success_ppct = summary.get("last_success_ppct")

        self._schedule_running = False
        self._update_schedule_resume_snapshot(
            resume_state=resume_state,
            params=self._schedule_resume_params if resume_state else None,
            persist=True,
        )
        self._set_schedule_button_states(
            running=False,
            can_resume=bool(self._schedule_resume_state),
        )
        self._set_auto_login_button_state()

        processed_count = completed + skipped_count + error_count
        if stopped:
            self.sched_progressbar["value"] = min(
                processed_count, self.sched_progressbar["maximum"]
            )
            self.lbl_sched_progress.config(
                text=(
                    f"⏸ Tạm dừng: {completed} nhập, {skipped_count} skip, "
                    f"{error_count} lỗi"
                )
            )
            self._update_sched_ppct_runtime(
                last_success=last_success_ppct,
                next_ppct=next_ppct,
                status="paused",
            )
            self._log(
                f"⏸ Schedule tạm dừng: {completed} thành công, "
                f"{skipped_count} đã có, {error_count} lỗi",
                "warning",
            )
            self._set_sched_live_progress(
                current=processed_count,
                total=int(float(self.sched_progressbar["maximum"] or 1)),
                phase="Schedule",
                detail="Đã tạm dừng an toàn, có thể bấm Tiếp tục từ checkpoint gần nhất",
                state="paused",
            )
        else:
            self.sched_progressbar["value"] = self.sched_progressbar["maximum"]
            self.lbl_sched_progress.config(
                text=(
                    f"✅ Hoàn tất: {completed} nhập, {skipped_count} skip, "
                    f"{error_count} lỗi"
                )
            )
            self._update_sched_ppct_runtime(
                last_success=last_success_ppct,
                next_ppct=next_ppct,
                status="done",
            )
            self._log(
                f"🏁 Schedule hoàn tất: {completed} thành công, "
                f"{skipped_count} đã có, {error_count} lỗi",
                "success" if error_count == 0 else "warning",
            )
            self._set_sched_live_progress(
                current=processed_count,
                total=int(float(self.sched_progressbar["maximum"] or 1)),
                phase="Schedule",
                detail="Đã xử lý hết các slot trong kế hoạch hiện tại",
                state="success" if error_count == 0 else "error",
            )

        pending_items = self._build_pending_schedule_items(
            self._schedule_resume_params,
            self._schedule_resume_state,
        )
        summary_payload = {
            **summary,
            "results": list(self._schedule_results),
            "pending_items": pending_items,
        }
        self._schedule_last_summary = summary_payload
        if completed > 0:
            self._invalidate_class_stats_cache()
            self._on_sched_progress_context_changed()
        if self._closing:
            return
        self._show_schedule_summary(summary_payload)

    # -----------------------------------------------------------------
    # SCROLL HELPERS
    # -----------------------------------------------------------------

    def _on_frame_configure(self, event):
        """Cập nhật scroll region khi nội dung main_frame thay đổi kích thước."""
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        """Đảm bảo main_frame luôn rộng bằng canvas (tránh khoảng trống)."""
        self._canvas.itemconfig(self._canvas_window, width=event.width)

    def _on_mousewheel(self, event):
        """Cuộn nội dung panel trái bằng mouse wheel."""
        self._canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _on_right_mousewheel(self, event):
        """Cuộn nội dung panel phải (Schedule) bằng mouse wheel."""
        self._right_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _bind_mousewheel_recursive(self, widget, handler):
        """Bind mousewheel handler cho widget và tất cả widget con (đệ quy).

        Đảm bảo scroll hoạt động khi chuột hover lên bất kỳ widget con nào
        trong panel, không chỉ trên canvas/frame gốc.
        """
        widget.bind("<MouseWheel>", handler)
        for child in widget.winfo_children():
            self._bind_mousewheel_recursive(child, handler)

    # -----------------------------------------------------------------
    # COMPACT / EXPAND
    # -----------------------------------------------------------------

    def _toggle_compact(self):
        """Toggle thu gọn / mở rộng."""
        self.is_compact = not self.is_compact

        if self.is_compact:
            # Thu gọn: chuyển sang mini dashboard thay vì để canvas trống.
            self._left_panel.grid_forget()
            self._right_panel.grid_forget()
            self._compact_shell.grid(row=0, column=0, columnspan=2, sticky="nsew")

            self.btn_compact.config(text="Mở đầy đủ")
            self._apply_window_geometry(compact=True, force=True)
            self._log("Thu gọn UI", "info")
        else:
            # Mở rộng: hiện lại theo thứ tự hiện hành
            self._compact_shell.grid_forget()
            self._left_panel.grid(row=0, column=0, sticky="nsew")
            self.frame_buttons.pack_forget()

            self.frame_cdp.pack(fill="x", pady=(0, 4))
            self.frame_buttons.pack(anchor="e", pady=(0, 4))
            self.frame_class_stats.pack(fill="x", pady=(0, 6))
            self.frame_log.pack(fill="x", expand=False, pady=(0, 0))
            self._right_panel.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
            self._canvas.yview_moveto(0)

            self.btn_compact.config(text="Thu gọn")
            self._apply_window_geometry(compact=False, force=True)
            self._log("Mở rộng UI", "info")

    # -----------------------------------------------------------------
    # CONFIG PERSISTENCE
    # -----------------------------------------------------------------

    def _save_config(self):
        """Lưu toàn bộ config ra JSON."""
        try:
            config = {
                "is_compact": self.is_compact,
                # Schedule (Lịch dạy) settings
                "sched_tuan_from": self.var_sched_tuan_from.get(),
                "sched_tuan_to": self.var_sched_tuan_to.get(),
                "sched_lop": self.var_sched_lop.get(),
                "sched_lop_multi": self.var_sched_lop_multi.get(),
                "sched_mode": self._get_sched_mode(),
                "sched_buoi": {str(k): v.get() for k, v in self._sched_buoi.items()},
                "sched_grid": {
                    f"{thu}_{buoi}": [v.get() for v in vars_list]
                    for (thu, buoi), vars_list in self._sched_grid.items()
                },
                # Schedule CDP fields (Phase 5)
                "sched_phan_mon": self.var_sched_phan_mon.get(),
                "sched_mon_hoc": self.var_sched_mon_hoc.get(),
                "sched_ppct_start": self.var_sched_ppct_start.get(),
                "sched_hs_nghi": self.var_sched_hs_nghi.get(),
                "sched_diem": self.var_sched_diem.get(),
                "sched_nhan_xet": self.var_sched_nhan_xet.get(),
                "sched_teacher_progress_fast_mode": bool(self.var_sched_teacher_progress_fast_mode.get()),
                "vnedu_username": self.var_vnedu_username.get(),
                "stats_tuan_from": self.var_stats_tuan_from.get(),
                "stats_tuan_to": self.var_stats_tuan_to.get(),
                "stats_mon_hoc": self.var_stats_mon_hoc.get(),
            }

            config_path = os.path.join(os.path.dirname(__file__), CONFIG_FILE)
            self._write_json_atomic(config_path, config)
            if self._schedule_resume_dirty:
                self._persist_schedule_resume_snapshot()
            self._save_class_stats_cache_to_disk()

        except Exception as e:
            self._log(f"Lỗi save config: {e}", "error")

    def _load_config(self):
        """Đọc config từ JSON, áp dụng vào UI."""
        config_path = os.path.join(os.path.dirname(__file__), CONFIG_FILE)
        if not os.path.exists(config_path):
            self._log("Chưa có config — dùng mặc định", "info")
            self._load_class_stats_cache_from_disk()
            self._load_schedule_resume_snapshot()
            return

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)

            # CDP port được khóa cứng để tránh lệch port giữa các lần chạy.
            self.var_cdp_port.set(DEFAULT_CDP_PORT)

            # Schedule (Lịch dạy) settings
            self.var_sched_tuan_from.set(cfg.get("sched_tuan_from", 1))
            self.var_sched_tuan_to.set(cfg.get("sched_tuan_to", 1))
            saved_sched_lop = cfg.get("sched_lop", "")
            if saved_sched_lop:
                self.var_sched_lop.set(saved_sched_lop)
            self.var_sched_lop_multi.set(cfg.get("sched_lop_multi", ""))
            self.var_sched_mode.set(
                cfg.get("sched_mode", SCHEDULE_MODE_MANUAL) or SCHEDULE_MODE_MANUAL
            )

            # Khôi phục buổi cho mỗi thứ
            saved_buoi = cfg.get("sched_buoi", {})
            for k_str, buoi_val in saved_buoi.items():
                try:
                    thu_key = int(k_str)
                    if thu_key in self._sched_buoi:
                        self._sched_buoi[thu_key].set(buoi_val)
                except (ValueError, KeyError):
                    pass

            # Khôi phục checkbox grid
            saved_grid = cfg.get("sched_grid", {})
            for key_str, bool_list in saved_grid.items():
                try:
                    parts = key_str.split("_")
                    thu_key = int(parts[0])
                    buoi_key = parts[1]
                    grid_key = (thu_key, buoi_key)
                    if grid_key in self._sched_grid:
                        for i, val in enumerate(bool_list):
                            if i < len(self._sched_grid[grid_key]):
                                self._sched_grid[grid_key][i].set(bool(val))
                except (ValueError, IndexError, KeyError):
                    pass

            # Trigger buổi change callback để sync checkboxes
            for thu in SCHEDULE_DAYS:
                self._on_sched_buoi_changed(thu)

            # Schedule CDP fields (Phase 5)
            saved_phan_mon = cfg.get("sched_phan_mon", "")
            if saved_phan_mon:
                self.var_sched_phan_mon.set(saved_phan_mon)
            saved_mon_hoc = cfg.get("sched_mon_hoc", "")
            if saved_mon_hoc:
                self.var_sched_mon_hoc.set(saved_mon_hoc)
            self.var_sched_ppct_start.set(cfg.get("sched_ppct_start", 1))
            self.var_sched_hs_nghi.set(cfg.get("sched_hs_nghi", "0"))
            self.var_sched_diem.set(cfg.get("sched_diem", "10"))
            self.var_sched_nhan_xet.set(
                cfg.get("sched_nhan_xet", "Lớp học chăm ngoan"))
            self.var_sched_teacher_progress_fast_mode.set(
                bool(cfg.get("sched_teacher_progress_fast_mode", False))
            )
            self.var_vnedu_username.set(cfg.get("vnedu_username", ""))
            self.var_stats_tuan_from.set(cfg.get("stats_tuan_from", 1))
            self.var_stats_tuan_to.set(cfg.get("stats_tuan_to", 1))
            self.var_stats_mon_hoc.set(cfg.get("stats_mon_hoc", ""))

            # Compact state
            if cfg.get("is_compact", False):
                self._toggle_compact()

            self._load_class_stats_cache_from_disk()
            self._load_schedule_resume_snapshot()
            self._log("Đã tải config", "success")

        except Exception as e:
            self._log(f"Lỗi load config: {e}", "warning")
            self._load_class_stats_cache_from_disk()
            self._load_schedule_resume_snapshot()

    # -----------------------------------------------------------------
    # WINDOW MANAGEMENT
    # -----------------------------------------------------------------

    @staticmethod
    def _coerce_window_geometry(current_rect, screen_rect, target_rect, margin=10, min_visible=120):
        """Chuẩn hóa geometry để cửa sổ không bị tụt khỏi màn hình hoặc co bất thường."""
        screen_x, screen_y, screen_w, screen_h = [int(v) for v in screen_rect]
        target_w, target_h, target_x, target_y = [int(v) for v in target_rect]

        max_w = max(220, screen_w - (margin * 2))
        max_h = max(220, screen_h - (margin * 2))
        target_w = max(220, min(target_w, max_w))
        target_h = max(220, min(target_h, max_h))
        target_x = min(max(target_x, screen_x + margin), screen_x + screen_w - target_w - margin)
        target_y = min(max(target_y, screen_y + margin), screen_y + screen_h - target_h - margin)

        if not current_rect:
            return target_w, target_h, target_x, target_y, True

        cur_x, cur_y, cur_w, cur_h = [int(v) for v in current_rect]
        compact_mode = target_h <= (COMPACT_HEIGHT + 20)
        min_width = target_w if not compact_mode else min(max(320, COMPACT_WIDTH), target_w)
        min_height = (
            target_h if compact_mode
            else min(target_h, max(520, int(target_h * 0.80)))
        )
        width_mismatch = abs(cur_w - target_w) > 8
        invalid_size = cur_w < min_width or cur_h < min_height or width_mismatch
        offscreen_h = (cur_x + min_visible) < screen_x or cur_x > (screen_x + screen_w - min_visible)
        offscreen_v = (cur_y + 40) < screen_y or cur_y > (screen_y + screen_h - min_visible)
        if invalid_size or offscreen_h or offscreen_v:
            return target_w, target_h, target_x, target_y, True

        clamped_x = min(max(cur_x, screen_x + margin), screen_x + screen_w - cur_w - margin)
        clamped_y = min(max(cur_y, screen_y + margin), screen_y + screen_h - cur_h - margin)
        changed = clamped_x != cur_x or clamped_y != cur_y
        return cur_w, cur_h, clamped_x, clamped_y, changed

    def _get_virtual_screen_bounds(self):
        """Lấy work-area bounds theo hệ tọa độ Tk, tránh bị taskbar che.

        Tk trên Windows thường chạy theo logical pixels (ví dụ màn 1920x1080
        có thể báo `winfo_screenheight() == 720`). Win32 trả work area theo
        physical pixels, nên phải quy đổi về cùng hệ tọa độ trước khi gọi
        `geometry()`.
        """
        try:
            user32 = ctypes.windll.user32
            phys_x = int(user32.GetSystemMetrics(76))  # SM_XVIRTUALSCREEN
            phys_y = int(user32.GetSystemMetrics(77))  # SM_YVIRTUALSCREEN
            phys_w = int(user32.GetSystemMetrics(78))  # SM_CXVIRTUALSCREEN
            phys_h = int(user32.GetSystemMetrics(79))  # SM_CYVIRTUALSCREEN

            class RECT(ctypes.Structure):
                _fields_ = [
                    ("left", ctypes.c_long),
                    ("top", ctypes.c_long),
                    ("right", ctypes.c_long),
                    ("bottom", ctypes.c_long),
                ]

            class MONITORINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", ctypes.c_ulong),
                    ("rcMonitor", RECT),
                    ("rcWork", RECT),
                    ("dwFlags", ctypes.c_ulong),
                ]

            hwnd = int(self.root.winfo_id())
            monitor = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)
            if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                rect = info.rcWork
            else:
                rect = RECT()
                if not user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
                    raise RuntimeError("SPI_GETWORKAREA failed")

            tk_w = max(int(self.root.winfo_screenwidth()), 1)
            tk_h = max(int(self.root.winfo_screenheight()), 1)
            scale_x = tk_w / max(phys_w, 1)
            scale_y = tk_h / max(phys_h, 1)
            x = int(round((int(rect.left) - phys_x) * scale_x))
            y = int(round((int(rect.top) - phys_y) * scale_y))
            w = int(round((int(rect.right) - int(rect.left)) * scale_x))
            h = int(round((int(rect.bottom) - int(rect.top)) * scale_y))
            if w > 0 and h > 0:
                return (x, y, w, h)
        except Exception:
            pass
        return (0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight())

    def _build_target_window_geometry(self, compact=None):
        """Tính geometry đích chuẩn cho mode compact/expanded hiện tại."""
        compact_mode = self.is_compact if compact is None else bool(compact)
        screen_x, screen_y, screen_w, screen_h = self._get_virtual_screen_bounds()
        target_w = COMPACT_WIDTH if compact_mode else EXPANDED_TOTAL_WIDTH
        target_w = min(target_w, max(320, screen_w - 50))
        target_h = (
            COMPACT_HEIGHT
            if compact_mode
            else min(EXPANDED_HEIGHT, max(screen_h - 96, 560))
        )
        target_h = min(target_h, max(220, screen_h - 72))
        target_x = screen_x + max(10, screen_w - target_w - 30)
        target_y = screen_y + 5
        return (screen_x, screen_y, screen_w, screen_h), (target_w, target_h, target_x, target_y)

    def _apply_window_geometry(self, compact=None, force=False):
        """Áp geometry hợp lệ và khóa width theo mode hiện tại để tránh cửa sổ bị co lệch."""
        try:
            self.root.deiconify()
        except Exception:
            pass
        self.root.update_idletasks()

        screen_rect, target_rect = self._build_target_window_geometry(compact=compact)
        try:
            current_rect = (
                int(self.root.winfo_x()),
                int(self.root.winfo_y()),
                int(self.root.winfo_width()),
                int(self.root.winfo_height()),
            )
        except Exception:
            current_rect = None

        compact_mode = self.is_compact if compact is None else bool(compact)
        if force:
            win_w, win_h, x, y = target_rect
            changed = True
        else:
            win_w, win_h, x, y, changed = self._coerce_window_geometry(
                current_rect=current_rect,
                screen_rect=screen_rect,
                target_rect=target_rect,
            )
        if force or changed:
            try:
                self.root.minsize(1, 1)
                self.root.maxsize(max(int(screen_rect[2]), win_w), max(int(screen_rect[3]), win_h))
            except Exception:
                pass
            self.root.geometry(f"{win_w}x{win_h}+{x}+{y}")
            self.root.update_idletasks()

        min_h = (
            COMPACT_HEIGHT
            if compact_mode
            else min(target_rect[1], max(520, int(target_rect[1] * 0.80)))
        )
        min_w = COMPACT_WIDTH if compact_mode else min(EXPANDED_TOTAL_WIDTH, max(860, win_w))
        self.root.minsize(min_w, min_h)
        try:
            max_w = win_w if compact_mode else max(int(screen_rect[2] - 20), min_w)
            self.root.maxsize(max_w, max(int(screen_rect[3] - 60), min_h))
        except Exception:
            pass

    def _ensure_window_visible(self, force=False):
        """Tự cứu geometry nếu cửa sổ bị co cực nhỏ hoặc trôi khỏi vùng nhìn thấy."""
        if self._closing or not self._root_exists():
            return
        self._apply_window_geometry(compact=self.is_compact, force=force)

    def _position_window(self):
        """Đặt cửa sổ gọn gàng phía bên phải màn hình.

        Chiều rộng = EXPANDED_TOTAL_WIDTH (vừa đủ 2-panel).
        Chiều cao = screen_height - 80px (trừ taskbar + padding).
        Vị trí: sát mép phải.
        """
        self._apply_window_geometry(compact=self.is_compact, force=True)

    def _has_active_background_work(self):
        """Kiểm tra còn worker nền nào đang chạy để đóng app an toàn hơn."""
        thread_flags = [
            bool(self._schedule_thread and self._schedule_thread.is_alive()),
            bool(self._class_stats_thread and self._class_stats_thread.is_alive()),
            bool(self._auto_login_thread and self._auto_login_thread.is_alive()),
            bool(self._delete_thread and self._delete_thread.is_alive()),
        ]
        return any(thread_flags) or any([
            self._schedule_running,
            self._class_stats_running,
            self._auto_login_running,
            self._quick_prepare_running,
            self._delete_running,
            not self._schedule_queue.empty(),
            not self._class_stats_queue.empty(),
            not self._delete_queue.empty(),
        ])

    def _finalize_close_when_idle(self):
        """Chờ worker flush checkpoint rồi mới hủy root."""
        if not self._root_exists():
            return
        elapsed_ms = (time.time() - float(self._close_started_at or time.time())) * 1000.0
        if self._has_active_background_work() and elapsed_ms < CLOSE_GRACE_PERIOD_MS:
            self.root.after(150, self._finalize_close_when_idle)
            return

        self._cancel_sched_teacher_progress_jobs()
        self._destroy_class_stats_dialog()
        self._destroy_delete_dialog()
        self._save_config()
        if self._ui_task_pump_after_id:
            try:
                self.root.after_cancel(self._ui_task_pump_after_id)
            except Exception:
                pass
            self._ui_task_pump_after_id = None
        self.root.destroy()

    def _on_close(self):
        """Xử lý đóng app: dừng schedule + save config."""
        if self._closing:
            return
        self._closing = True
        self._close_started_at = time.time()
        self._cancel_sched_teacher_progress_jobs()
        self._sched_teacher_progress_request_id += 1
        if self._schedule_running:
            self._request_schedule_stop("đóng ứng dụng")
        if self._delete_running:
            self._delete_stop_event.set()
        self.root.after(150, self._finalize_close_when_idle)

    def run(self):
        """Chạy main loop."""
        self._log("App khởi động — sẵn sàng", "success")
        self.root.mainloop()


# =====================================================================
# PHẦN 4: ENTRY POINT
# =====================================================================

if __name__ == "__main__":
    app = AutoDaNangApp()
    app.run()

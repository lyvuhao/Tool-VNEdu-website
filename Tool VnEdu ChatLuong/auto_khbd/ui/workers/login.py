"""Worker đăng nhập VnEdu tự động và mở trang KHDH."""

from __future__ import annotations

import os as _os
import queue
import threading
import time
import traceback

from ...engine.bootstrap import fetch_bootstrap
from ...engine.client.client import KHDHClient
from ..chrome_launcher import find_chrome_executable, is_cdp_port_open, launch_chrome_with_debug


VNEDU_KHDH_URL = _os.environ.get(
    "VNEDU_KHDH_URL",
    "https://vemzezsoasgdsoctrang.vnedu.vn/v5/",
)


VNEDU_SSO_URL  = "https://user.vnedu.vn/sso/"


# Error message dùng chung khi `_open_khdh_module` fail — phân biệt 2 nguyên nhân:
# - Desktop chưa render shortcut (JS check fail)
# - Click shortcut fail 3 lần (có thể bị che bởi popup khác / app freeze)
_KHDH_OPEN_FAIL_MSG = (
    "Không mở được module Kế hoạch dạy học sau 3 lần thử.\n\n"
    "Có thể do:\n"
    "  • Desktop VnEdu chưa render xong (mạng chậm)\n"
    "  • Có popup/alert đang che shortcut\n"
    "  • Trình duyệt đang freeze\n\n"
    "Cách khắc phục:\n"
    "  1. Đợi 5-10 giây rồi bấm [Đăng nhập] lại trên tool\n"
    "  2. Hoặc click thủ công vào shortcut \"Kế hoạch dạy học\" "
    "trên web rồi thử lại\n"
    "  3. Hoặc reload tab VnEdu rồi thử lại"
)


# JS: tìm shortcut "Kế hoạch dạy học" trên dashboard VnEdu
_JS_FIND_KHDH_SHORTCUT = r"""
() => {
    // Tìm tất cả element có text chứa "Kế hoạch" hoặc "khdh" (case-insensitive)
    const candidates = Array.from(document.querySelectorAll('a, div, span, li, td'));
    for (const el of candidates) {
        const txt = (el.innerText || el.textContent || '').trim();
        if (txt.includes('Kế hoạch dạy học') || txt.includes('khdh') ||
            txt.toLowerCase().includes('k\u1ebf ho\u1ea1ch d\u1ea1y h\u1ecdc')) {
            // Tìm element clickable gần nhất
            const clickable = el.closest('a') || el.closest('[onclick]') || el;
            const rect = clickable.getBoundingClientRect();
            if (rect.width > 0 && rect.height > 0) {
                return {
                    found: true,
                    text: txt.substring(0, 50),
                    x: rect.left + rect.width / 2,
                    y: rect.top + rect.height / 2,
                };
            }
        }
    }
    return {found: false};
}
"""


class LoginWorker(threading.Thread):
    """Đăng nhập VnEdu tự động + mở module KHDH + fetch bootstrap.

    Flow:
        1. Spawn Chrome debug (nếu chưa có)
        2. Navigate đến VNEDU_KHDH_URL → redirect về SSO login
        3. Fill tài khoản + mật khẩu → click Đăng nhập
        4. Đợi redirect về /v5/ (max 30s)
        5. Tìm shortcut "Kế hoạch dạy học" → click
        6. Đợi module KHDH load (URL chứa /v5/ + ExtJS ready)
        7. Fetch bootstrap → emit done

    Events:
        ("status", text)
        ("ctx", KHDHContext)
        ("done", BootstrapData)
        ("error", message)
        ("login_failed", message)   — sai tk/mk hoặc captcha
        ("need_password", message)  — Chrome chưa đăng nhập mà chưa nhập mật khẩu

    `password` có thể rỗng: khi Chrome đã đăng nhập VnEdu (vd. từ dashboard) thì không cần
    điền form đăng nhập. `url`: trang VnEdu của trường (mặc định VNEDU_KHDH_URL).
    """

    def __init__(self, username: str, password: str, port: int,
                 event_queue: queue.Queue, url: str | None = None):
        super().__init__(daemon=True, name="KHDH-Login")
        self.username = username
        self.password = password
        self.port = port
        self.q = event_queue
        self.url = url or VNEDU_KHDH_URL

    def run(self):
        """Xử lý thông minh theo trạng thái hiện tại của Chrome/VnEdu.

        Các trường hợp:
          Case A: Chrome chưa mở → spawn + login + mở KHDH
          Case B: Chrome mở, KHDH đã mở sẵn → fetch bootstrap luôn
          Case C: Chrome mở, đã ở /v5/ nhưng chưa mở KHDH → click shortcut
          Case D: Chrome mở, đang ở SSO login → fill form + redirect + mở KHDH
          Case E: Chrome mở, trang khác VnEdu → navigate /v5/ + mở KHDH
          Case F: Chrome mở, không có tab VnEdu → mở tab mới + login
        """
        try:
            from playwright.sync_api import sync_playwright

            # ── Stage 1: Spawn Chrome nếu chưa có ──────────────────────
            chrome_was_absent = not is_cdp_port_open(self.port, timeout=0.5)
            if chrome_was_absent:
                self.q.put(("status", "Đang khởi động Chrome…"))
                chrome_path = find_chrome_executable()
                if not chrome_path:
                    self.q.put(("error",
                        "Không tìm thấy Chrome/Edge trên máy.\n"
                        "Hãy cài Google Chrome rồi thử lại."))
                    return
                ok = launch_chrome_with_debug(
                    self.port, url=self.url, chrome_path=chrome_path
                )
                if not ok:
                    self.q.put(("error", "Không khởi động được Chrome."))
                    return
                for _ in range(40):
                    if is_cdp_port_open(self.port, timeout=0.3):
                        break
                    time.sleep(0.3)
                else:
                    self.q.put(("error",
                        f"Chrome đã mở nhưng cổng {self.port} chưa sẵn sàng."))
                    return

            # ── Stage 2: Connect Playwright ─────────────────────────────
            self.q.put(("status", "Đang kết nối Chrome…"))
            pw = sync_playwright().start()
            try:
                browser = pw.chromium.connect_over_cdp(
                    f"http://localhost:{self.port}", timeout=10000
                )
            except Exception as e:
                try: pw.stop()
                except Exception: pass
                self.q.put(("error",
                    f"Không kết nối được Chrome (cổng {self.port}).\n{e}"))
                return

            try:
                # ── Stage 3: Phát hiện trạng thái hiện tại ──────────────
                page = self._get_or_open_vnedu_tab(browser)
                if page is None:
                    self.q.put(("error", "Không mở được tab VnEdu."))
                    return

                cur_url = (page.url or "").lower()
                self.q.put(("status", f"Phát hiện trạng thái: {page.url[:60]}…"))

                # ── Case B: KHDH đã mở sẵn → verify ready rồi fetch ────
                if self._is_khdh_already_open(page):
                    self.q.put(("status",
                        "✓ Module KHDH đã mở sẵn — đang tải dữ liệu…"))
                    # L1 fix: verify lại readiness trước khi fetch
                    # (window có thể đóng giữa check và fetch)
                    if not self._wait_khdh_ready(page, timeout_s=10):
                        self.q.put(("status",
                            "Module KHDH vừa đóng — đang mở lại…"))
                        self._open_khdh_module(page)
                        if not self._wait_khdh_ready(page, timeout_s=30):
                            self.q.put(("error",
                                "Module KHDH không sẵn sàng. Hãy thử lại."))
                            return
                    self._do_fetch_bootstrap(page)
                    return

                # ── Case C: Đã ở /v5/ nhưng chưa mở KHDH ───────────────
                if "/v5/" in cur_url:
                    self.q.put(("status",
                        "Đã đăng nhập VnEdu — đang mở module KHDH…"))
                    # L2 fix: block nếu click fail
                    if not self._open_khdh_module(page):
                        self.q.put(("error", _KHDH_OPEN_FAIL_MSG))
                        return
                    if not self._wait_khdh_ready(page, timeout_s=30):
                        self.q.put(("error",
                            "Module KHDH chưa load xong sau 30s.\n"
                            "Hãy thử lại."))
                        return
                    self._do_fetch_bootstrap(page)
                    return

                # ── Case D: Đang ở SSO login ─────────────────────────────
                if "sso" in cur_url or "login" in cur_url or "user.vnedu" in cur_url:
                    self.q.put(("status", "Đang đăng nhập VnEdu…"))
                    if not self._do_login(page):
                        return
                    ok_v5 = self._wait_for_v5(page, timeout_s=30)
                    # L4 fix: _wait_for_v5 chỉ emit login_failed, caller emit error
                    if not ok_v5:
                        # Kiểm tra xem có phải login_failed không
                        # (đã emit bởi _wait_for_v5) hay timeout thật
                        self.q.put(("error",
                            "Không vào được trang KHDH sau khi đăng nhập.\n"
                            "Hãy kiểm tra tài khoản và mật khẩu."))
                        return
                    self.q.put(("status", "Đang mở module KHDH…"))
                    if not self._open_khdh_module(page):
                        self.q.put(("error", _KHDH_OPEN_FAIL_MSG))
                        return
                    if not self._wait_khdh_ready(page, timeout_s=30):
                        self.q.put(("error",
                            "Module KHDH chưa load xong sau 30s.\n"
                            "Hãy thử lại."))
                        return
                    self._do_fetch_bootstrap(page)
                    return

                # ── Case E/F: Trang khác hoặc tab mới → navigate + login ─
                self.q.put(("status", "Đang mở trang VnEdu…"))
                try:
                    page.goto(self.url, timeout=20000,
                             wait_until="domcontentloaded")
                except Exception:
                    pass

                cur_url = (page.url or "").lower()

                # Sau navigate: check lại trạng thái
                if "/v5/" in cur_url:
                    # Đã login sẵn → mở KHDH
                    self.q.put(("status", "Đã đăng nhập — đang mở KHDH…"))
                    if not self._open_khdh_module(page):
                        self.q.put(("error", _KHDH_OPEN_FAIL_MSG))
                        return
                elif "sso" in cur_url or "user.vnedu" in cur_url:
                    # Cần login
                    self.q.put(("status", "Đang đăng nhập VnEdu…"))
                    if not self._do_login(page):
                        return
                    if not self._wait_for_v5(page, timeout_s=30):
                        self.q.put(("error",
                            "Không vào được /v5/ sau khi đăng nhập."))
                        return
                    self.q.put(("status", "Đang mở module KHDH…"))
                    if not self._open_khdh_module(page):
                        self.q.put(("error", _KHDH_OPEN_FAIL_MSG))
                        return
                else:
                    self.q.put(("error",
                        f"Trang không nhận ra: {page.url}\n"
                        "Hãy mở Chrome và vào VnEdu thủ công rồi thử lại."))
                    return

                if not self._wait_khdh_ready(page, timeout_s=30):
                    self.q.put(("error",
                        "Module KHDH chưa load xong sau 30s.\n"
                        "Hãy thử lại."))
                    return
                self._do_fetch_bootstrap(page)

            finally:
                try: pw.stop()
                except Exception: pass

        except Exception as e:
            tb = traceback.format_exc()
            self.q.put(("error", f"Lỗi không lường trước: {e}\n\n{tb}"))

    def _is_khdh_already_open(self, page) -> bool:
        """Kiểm tra module KHDH đã mở và sẵn sàng chưa."""
        try:
            res = page.evaluate("""
() => {
    try {
        var khdhOpen = false;
        Ext.WindowMgr.each(function(w) {
            if (w.title && w.title.indexOf('K\u1EBF ho\u1EA1ch d\u1EA1y h\u1ECDc') >= 0
                    && w.isVisible && w.isVisible()) {
                khdhOpen = true;
            }
        });
        if (!khdhOpen) return false;
        // Check combobox tuần
        var w = Ext.ComponentQuery.query('combobox').find(function(c) {
            return (c.getName ? c.getName() : c.name) === 'cboTuanHoc';
        });
        return !!(w && w.getValue && w.getValue());
    } catch(e) { return false; }
}
""")
            return bool(res)
        except Exception:
            return False

    def _do_fetch_bootstrap(self, page):
        """Fetch context + bootstrap và emit events."""
        client = KHDHClient(page)
        self.q.put(("status", "Đang đọc thông tin tài khoản…"))
        try:
            ctx = client.fetch_context()
        except Exception as e:
            self.q.put(("error",
                f"Không đọc được thông tin tài khoản.\n{e}"))
            return
        self.q.put(("ctx", ctx))

        self.q.put(("status", "Đang lấy danh sách lớp / môn / phân môn…"))
        try:
            bootstrap_data = fetch_bootstrap(client)
        except Exception as e:
            tb = traceback.format_exc()
            self.q.put(("error",
                f"Lỗi khi lấy danh sách: {e}\n\n{tb}"))
            return

        if bootstrap_data.is_empty():
            self.q.put(("error",
                "Tài khoản chưa được phân công lớp dạy nào.\n"
                "Liên hệ quản trị nhà trường để kiểm tra phân công."))
            return

        self.q.put(("status",
            f"✓ Đã lấy {len(bootstrap_data.lop_options)} lớp, "
            f"{sum(len(v) for v in bootstrap_data.mon_by_lop.values())} môn."))
        self.q.put(("done", bootstrap_data))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_or_open_vnedu_tab(self, browser):
        """Tìm tab VnEdu đang mở, hoặc mở tab mới.

        Ưu tiên: tab đang ở /v5/ > tab vnedu bất kỳ > tab mới.
        """
        # Ưu tiên tab đang ở /v5/
        for ctx in browser.contexts:
            for p in ctx.pages:
                url = (p.url or "").lower()
                if "vnedu" in url and "/v5/" in url:
                    return p
        # Tab vnedu bất kỳ (đang ở SSO login)
        for ctx in browser.contexts:
            for p in ctx.pages:
                url = (p.url or "").lower()
                if "vnedu" in url:
                    return p
        # Mở tab mới trong context đầu tiên
        try:
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            page = ctx.new_page()
            return page
        except Exception:
            return None

    def _do_login(self, page) -> bool:
        """Fill form login VnEdu. Returns True nếu submit thành công."""
        if not self.username or not self.password:
            # Không gửi form rỗng (tránh bị tính là đăng nhập sai) -> nhờ người dùng nhập.
            self.q.put(("need_password",
                "Chrome chưa đăng nhập VnEdu (hoặc phiên đã hết hạn).\n"
                "Hãy nhập tài khoản và mật khẩu rồi bấm [Đăng nhập VnEdu]."))
            return False
        try:
            # Đợi form login xuất hiện
            page.wait_for_selector(
                'input[placeholder="Tài khoản"]', timeout=10000
            )
            # Fill tài khoản
            page.fill('input[placeholder="Tài khoản"]', self.username)
            time.sleep(0.3)
            # Fill mật khẩu
            page.fill('input[placeholder="Mật khẩu"]', self.password)
            time.sleep(0.3)
            # Click Đăng nhập
            page.click('button:has-text("Đăng nhập")')
            # (#4) Đã submit xong → xóa plaintext khỏi worker. Không path nào
            # dùng lại self.password sau đây; nếu login fail user sẽ nhập lại
            # từ UI (tạo LoginWorker mới).
            self.password = ""
            return True
        except Exception as e:
            self.q.put(("error",
                f"Không điền được form đăng nhập.\n"
                f"Trang login có thể đã thay đổi.\n\nChi tiết: {e}"))
            return False

    def _wait_for_v5(self, page, timeout_s: int = 30) -> bool:
        """Đợi URL chứa /v5/ (sau khi login redirect).

        Nếu đã ở /v5/ rồi (session cũ còn hiệu lực) → return True ngay.
        """
        # Check ngay lập tức
        if "/v5/" in (page.url or "").lower():
            return True

        deadline = time.monotonic() + timeout_s
        last_status_at = -10.0
        while time.monotonic() < deadline:
            url = (page.url or "").lower()
            if "/v5/" in url:
                return True

            # Countdown status mỗi 5s (L5 fix)
            elapsed = timeout_s - (deadline - time.monotonic())
            if elapsed - last_status_at >= 5:
                remaining = int(deadline - time.monotonic())
                self.q.put(("status",
                    f"Đang đợi đăng nhập VnEdu… (còn {remaining}s)"))
                last_status_at = elapsed

            # Kiểm tra lỗi đăng nhập — dùng selector hẹp (L3 fix)
            # Tránh false-positive từ class*="error" match quá rộng
            try:
                err_el = page.query_selector(
                    '.error-message, .alert-danger, .login-error, '
                    '.x-form-invalid-msg, [id*="errorMsg"]'
                )
                if err_el:
                    err_text = (err_el.inner_text() or "").strip()
                    if err_text and len(err_text) > 3:
                        # L4 fix: chỉ emit login_failed — caller xử lý error
                        self.q.put(("login_failed",
                            f"Đăng nhập thất bại: {err_text}\n"
                            "Hãy kiểm tra lại tài khoản và mật khẩu."))
                        return False
            except Exception:
                pass
            time.sleep(0.5)
        return False

    def _open_khdh_module(self, page) -> bool:
        """Mở module KHDH — click trực tiếp vào DOM element shortcut.

        Flow siết logic:
        1. **Đợi desktop render xong** (`_wait_for_desktop_ready` max 15s) —
           tránh race condition: tool click ngay sau redirect /v5/ trong khi
           ExtJS chưa init xong các shortcut → click fail.
        2. **Retry 3 lần** với 1.5s delay giữa các lần — server render chậm
           có thể làm 3 method (jQuery click / fireEvent / id click) đều
           fail tạm thời, retry thường thành công.
        3. Dùng 3 phương pháp theo thứ tự ưu tiên trong mỗi lần thử:
           a) Click DOM element có class 'lichbaogiang' (chính xác nhất)
           b) fireEvent ExtJS itemclick (nếu dataview đã bind)
           c) Click element có id chứa 'Kế hoạch dạy học'
        """
        # Wait desktop render xong trước khi click
        if not self._wait_for_desktop_ready(page, timeout_s=15):
            self.q.put((
                "status",
                "⚠ Desktop VnEdu chưa render shortcut nào sau 15s",
            ))
            return False

        _JS_CLICK_KHDH = """
() => {
    try {
        // Method 1: Click DOM element trực tiếp (robust nhất)
        var iconEl = document.querySelector('.ux-desktop-shortcut-icon.lichbaogiang');
        if (iconEl) {
            var shortcutDiv = iconEl.closest('.ux-desktop-shortcut');
            if (shortcutDiv) {
                // Trigger jQuery click event (web dùng jQuery để bind)
                if (typeof jQuery !== 'undefined') {
                    jQuery(shortcutDiv).trigger('click');
                    return {ok: true, method: 'jquery_click', el: shortcutDiv.id};
                }
                // Fallback: native click
                shortcutDiv.click();
                return {ok: true, method: 'native_click', el: shortcutDiv.id};
            }
        }

        // Method 2: fireEvent ExtJS itemclick
        var dv = null;
        Ext.ComponentQuery.query('dataview').forEach(function(c) {
            if (c.store && c.store.getCount && c.store.getCount() > 5) dv = c;
        });
        if (dv && dv.store) {
            var rec = null;
            dv.store.each(function(r) {
                if (r.data.open_class === 'MyDesktop.Edu.Lich_Bao_Giang' ||
                    r.data.iconCls === 'lichbaogiang') rec = r;
            });
            if (rec) {
                dv.fireEvent('itemclick', dv, rec);
                return {ok: true, method: 'extjs_fireEvent', name: rec.data.name};
            }
        }

        // Method 3: click by id pattern
        var byId = document.getElementById('K\u1EBF ho\u1EA1ch d\u1EA1y h\u1ECDc-shortcut');
        if (byId) {
            if (typeof jQuery !== 'undefined') {
                jQuery(byId).trigger('click');
            } else {
                byId.click();
            }
            return {ok: true, method: 'id_click'};
        }

        return {ok: false, err: 'all methods failed'};
    } catch(e) {
        return {ok: false, err: e.message};
    }
}
"""
        last_err = ""
        for attempt in range(1, 4):
            try:
                res = page.evaluate(_JS_CLICK_KHDH)
                if res and res.get("ok"):
                    self.q.put((
                        "status",
                        f"Đã click shortcut KHDH ({res.get('method', '?')}, "
                        f"lần {attempt})",
                    ))
                    time.sleep(2.0)  # đợi module load
                    return True
                last_err = (res or {}).get("err", "no response")
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
            # Không phải lần cuối → đợi rồi thử lại
            if attempt < 3:
                self.q.put((
                    "status",
                    f"Click shortcut KHDH lần {attempt} chưa thành công ({last_err}), thử lại…",
                ))
                time.sleep(1.5)
        self.q.put((
            "status",
            f"⚠ Click shortcut KHDH thất bại sau 3 lần: {last_err}",
        ))
        return False

    def _wait_for_desktop_ready(self, page, timeout_s: int = 15) -> bool:
        """Đợi desktop /v5/ render xong các shortcut (ít nhất 1 visible).

        Tránh race: sau redirect /v5/, ExtJS cần 1-3s để init Desktop.
        Click ngay sẽ fail vì DOM/ExtJS components chưa tồn tại.

        Returns True nếu desktop sẵn sàng, False nếu timeout.
        """
        _JS_DESKTOP_READY = """
() => {
    try {
        // Check 1: có shortcut DOM element và visible
        const shortcuts = document.querySelectorAll('.ux-desktop-shortcut');
        if (shortcuts.length === 0) {
            return {ready: false, reason: 'no shortcuts in DOM'};
        }
        let visible = 0;
        for (const sc of shortcuts) {
            if (sc.offsetParent !== null) visible++;
        }
        if (visible === 0) {
            return {ready: false, reason: 'shortcuts hidden'};
        }
        // Check 2: ExtJS dataview đã có data (cho method 2)
        // Dùng để confirm desktop đã hoàn tất bind data, không chỉ render HTML
        let dvCount = 0;
        try {
            if (typeof Ext !== 'undefined' && Ext.ComponentQuery) {
                Ext.ComponentQuery.query('dataview').forEach(function(c) {
                    if (c.store && c.store.getCount) {
                        dvCount = Math.max(dvCount, c.store.getCount());
                    }
                });
            }
        } catch(e) {}
        return {ready: true, dom: visible, ext: dvCount};
    } catch(e) {
        return {ready: false, reason: e.message};
    }
}
"""
        deadline = time.monotonic() + timeout_s
        last_status_at = -10.0
        last_reason = ""
        while time.monotonic() < deadline:
            try:
                res = page.evaluate(_JS_DESKTOP_READY)
                if res and res.get("ready"):
                    self.q.put((
                        "status",
                        f"Desktop VnEdu sẵn sàng "
                        f"({res.get('dom', 0)} shortcut, "
                        f"ExtJS {res.get('ext', 0)} item)",
                    ))
                    return True
                last_reason = res.get("reason", "") if res else ""
            except Exception as e:
                last_reason = f"{type(e).__name__}: {e}"
            elapsed = timeout_s - (deadline - time.monotonic())
            if elapsed - last_status_at >= 3:
                remaining = int(deadline - time.monotonic())
                self.q.put((
                    "status",
                    f"Đang đợi desktop VnEdu render… (còn {remaining}s)",
                ))
                last_status_at = elapsed
            time.sleep(0.5)
        self.q.put((
            "status",
            f"⚠ Desktop chưa render xong sau {timeout_s}s ({last_reason})",
        ))
        return False

    def _wait_khdh_ready(self, page, timeout_s: int = 30) -> bool:
        """Đợi module KHDH load xong — combobox tuần xuất hiện.

        Check 2 điều kiện:
        1. Window KHDH visible (title chứa 'Kế hoạch dạy học')
        2. ExtJS combobox cboTuanHoc có giá trị (module đã load data)
        """
        _JS_CHECK_KHDH_READY = """
() => {
    try {
        // Check 1: window KHDH visible
        var khdhWinVisible = false;
        Ext.WindowMgr.each(function(w) {
            if (w.title && w.title.indexOf('K\u1EBF ho\u1EA1ch d\u1EA1y h\u1ECDc') >= 0
                    && w.isVisible && w.isVisible()) {
                khdhWinVisible = true;
            }
        });
        if (!khdhWinVisible) return {ready: false, reason: 'window not visible'};

        // Check 2: combobox tuần có giá trị
        var w = Ext.ComponentQuery.query('combobox').find(function(c) {
            return (c.getName ? c.getName() : c.name) === 'cboTuanHoc';
        });
        if (!w) return {ready: false, reason: 'cboTuanHoc not found'};
        var val = w.getValue ? w.getValue() : null;
        if (!val) return {ready: false, reason: 'cboTuanHoc has no value'};

        return {ready: true, tuan: val};
    } catch(e) {
        return {ready: false, reason: e.message};
    }
}
"""
        deadline = time.monotonic() + timeout_s
        last_reason = ""
        last_status_at = -10.0
        while time.monotonic() < deadline:
            try:
                res = page.evaluate(_JS_CHECK_KHDH_READY)
                if res and res.get("ready"):
                    self.q.put(("status",
                        f"Module KHDH sẵn sàng (tuần {res.get('tuan', '?')})"))
                    return True
                last_reason = res.get("reason", "") if res else ""
            except Exception:
                pass
            # G9 fix: countdown status mỗi 5s
            elapsed = timeout_s - (deadline - time.monotonic())
            if elapsed - last_status_at >= 5:
                remaining = int(deadline - time.monotonic())
                self.q.put(("status",
                    f"Đang đợi module KHDH khởi động… (còn {remaining}s)"))
                last_status_at = elapsed
            time.sleep(0.8)
        self.q.put(("status",
            f"⚠ KHDH chưa ready sau {timeout_s}s: {last_reason}"))
        return False

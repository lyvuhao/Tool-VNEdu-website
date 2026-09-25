"""Điều hướng tới màn Chi tiết sổ đầu bài."""

from .config import VNEDU_HOME_URL, VNEDU_SSO_LOGIN_URL


class NavigationMixin:
    """Điều hướng tới màn Chi tiết sổ đầu bài."""

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

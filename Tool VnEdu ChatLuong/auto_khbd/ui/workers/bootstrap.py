"""Worker kết nối Chrome và tải dữ liệu khởi tạo."""

from __future__ import annotations

import queue
import threading
import time
import traceback

from ...engine.bootstrap import fetch_bootstrap
from ...engine.client.client import KHDHClient
from ..chrome_launcher import find_chrome_executable, is_cdp_port_open, launch_chrome_with_debug


# =====================================================================
# Worker — BootstrapWorker
# =====================================================================

class _BrowserWithPlaywright:
    """Wrapper để return cả `browser` và `pw` từ helper, hỗ trợ `with`.

    Khi caller dùng `with wrapper:`, __exit__ sẽ stop playwright + close
    browser. Nếu launched=True, KHÔNG đóng browser (Chrome do tool spawn,
    user vẫn cần dùng) — chỉ disconnect Playwright.
    """
    def __init__(self, browser, pw, launched: bool = False):
        self._browser = browser
        self._pw = pw
        self._launched = launched

    def __enter__(self):
        return self._browser

    def __exit__(self, exc_type, exc_val, exc_tb):
        # KHÔNG close browser khi tool tự launch — user còn dùng
        # Khi connect đến Chrome có sẵn, cũng KHÔNG close vì tool chỉ là
        # debug client, không sở hữu Chrome process.
        try:
            self._pw.stop()
        except Exception:
            pass
        return False

    @property
    def contexts(self):
        return self._browser.contexts


class BootstrapWorker(threading.Thread):
    """Connect Chrome CDP và fetch danh sách lớp/môn/phân môn từ web.

    Events emit qua queue:
        ("status", text)         — cập nhật dòng trạng thái
        ("ctx", KHDHContext)     — đã lấy được context (token, GV, năm học)
        ("done", BootstrapData)  — hoàn tất
        ("error", message)       — lỗi
    """

    def __init__(self, port: int, event_queue: queue.Queue,
                 force_refresh: bool = False):
        super().__init__(daemon=True, name="KHDH-Bootstrap")
        self.port = port
        self.q = event_queue
        self.force_refresh = force_refresh

    def run(self):
        try:
            # ---- Stage 1: Connect CDP — auto-launch Chrome nếu chưa mở ----
            browser = self._connect_with_auto_launch()
            if browser is None:
                return  # error đã được put vào queue

            with browser:
                page = self._wait_for_vnedu_page(browser)
                if not page:
                    return  # error đã put

                try:
                    page.bring_to_front()
                except Exception:
                    pass

                client = KHDHClient(page)

                self.q.put(("status", "Đang đọc thông tin tài khoản…"))
                try:
                    ctx = client.fetch_context()
                except Exception as e:
                    self.q.put((
                        "error",
                        f"Không đọc được thông tin tài khoản. "
                        f"Có thể bạn chưa đăng nhập VnEdu.\n\nChi tiết: {e}",
                    ))
                    return
                self.q.put(("ctx", ctx))

                self.q.put(("status", "Đang lấy danh sách lớp / môn / phân môn từ web…"))
                try:
                    bootstrap_data = fetch_bootstrap(client)
                except Exception as e:
                    tb = traceback.format_exc()
                    self.q.put((
                        "error",
                        f"Lỗi khi lấy danh sách: {type(e).__name__}: {e}\n\n{tb}",
                    ))
                    return

                if bootstrap_data.is_empty():
                    self.q.put((
                        "error",
                        "Tài khoản chưa được phân công lớp dạy nào.\n"
                        "Liên hệ quản trị nhà trường để kiểm tra phân công.",
                    ))
                    return

                self.q.put((
                    "status",
                    f"Đã lấy {len(bootstrap_data.lop_options)} lớp, "
                    f"{sum(len(v) for v in bootstrap_data.mon_by_lop.values())} môn. "
                    "Web đã chuyển về chế độ 'Sửa'."
                ))
                self.q.put(("done", bootstrap_data))
        except Exception as e:
            tb = traceback.format_exc()
            self.q.put(("error", f"Lỗi không lường trước: {e}\n\n{tb}"))

    def _connect_with_auto_launch(self):
        """Connect CDP — nếu fail, auto-launch Chrome rồi retry max 60s.

        Trả về browser object đã wrap trong sync_playwright context, hoặc
        None nếu fail (đã put error vào queue).

        Lưu ý: caller phải dùng `with browser:` để sync_playwright cleanup.
        """
        from playwright.sync_api import sync_playwright

        # Probe nhanh: nếu CDP đã sẵn sàng thì connect ngay
        cdp_ready = is_cdp_port_open(self.port, timeout=0.5)
        launched = False

        if not cdp_ready:
            # Auto-launch Chrome
            self.q.put((
                "status",
                f"Chrome chưa mở trên cổng {self.port} — đang khởi động Chrome…",
            ))
            chrome_path = find_chrome_executable()
            if not chrome_path:
                self.q.put((
                    "error",
                    "Không tìm thấy Chrome trên máy.\n\n"
                    "Hãy cài Google Chrome (hoặc Microsoft Edge) rồi thử lại.\n"
                    "Hoặc tự mở Chrome bằng lệnh:\n"
                    f"  chrome.exe --remote-debugging-port={self.port}",
                ))
                return None
            ok = launch_chrome_with_debug(self.port, chrome_path=chrome_path)
            if not ok:
                self.q.put((
                    "error",
                    "Không khởi động được Chrome.\n\n"
                    "Hãy thử tự mở Chrome bằng lệnh:\n"
                    f"  chrome.exe --remote-debugging-port={self.port}",
                ))
                return None
            launched = True
            self.q.put((
                "status",
                "Đã mở Chrome — vui lòng đăng nhập VnEdu trong cửa sổ vừa mở…",
            ))

            # Đợi CDP port sẵn sàng (Chrome boot có thể mất 1-3s)
            for _ in range(30):  # 30 * 0.3s = 9s
                if is_cdp_port_open(self.port, timeout=0.3):
                    cdp_ready = True
                    break
                time.sleep(0.3)
            if not cdp_ready:
                self.q.put((
                    "error",
                    "Chrome đã khởi động nhưng cổng debug chưa sẵn sàng "
                    f"(cổng {self.port}). Hãy thử lại.",
                ))
                return None

        # CDP đã sẵn sàng — connect Playwright
        # KHÔNG dùng `with sync_playwright() as pw:` vì cần return browser
        # ra ngoài context. Dùng manual __enter__/__exit__ trick:
        # Thay vào đó wrap toàn bộ logic vào helper class.
        try:
            pw = sync_playwright().start()
        except Exception as e:
            self.q.put((
                "error",
                f"Không khởi tạo được Playwright: {e}",
            ))
            return None

        try:
            browser = pw.chromium.connect_over_cdp(
                f"http://localhost:{self.port}",
                timeout=10000,
            )
        except Exception as e:
            try:
                pw.stop()
            except Exception:
                pass
            self.q.put((
                "error",
                f"Không kết nối được Chrome trên cổng {self.port}.\n\n"
                f"Chi tiết: {type(e).__name__}: {str(e)[:150]}",
            ))
            return None

        # Wrap browser + pw để cleanup khi caller `with` exit
        return _BrowserWithPlaywright(browser, pw, launched=launched)

    def _wait_for_vnedu_page(self, browser):
        """Tìm tab VnEdu /v5/ — nếu chưa có, đợi user đăng nhập + nav tới (max 90s).

        Khi auto-launch Chrome, user vẫn đang ở trang login → tab v5 chưa
        tồn tại. Polling đến khi có hoặc timeout.
        """
        # Thử ngay lần đầu
        page = self._find_vnedu_page(browser)
        if page:
            return page

        # Polling 90s — đủ user đăng nhập + nav tới module KHDH
        max_wait_s = 90
        tick = 1.0
        elapsed = 0.0
        last_status_at = -10
        while elapsed < max_wait_s:
            page = self._find_vnedu_page(browser)
            if page:
                return page
            # Update status mỗi 5s
            if elapsed - last_status_at >= 5:
                remaining = int(max_wait_s - elapsed)
                self.q.put((
                    "status",
                    f"Vui lòng đăng nhập VnEdu và mở module Kế hoạch dạy học "
                    f"(còn {remaining}s)…",
                ))
                last_status_at = elapsed
            time.sleep(tick)
            elapsed += tick

        self.q.put((
            "error",
            "Hết thời gian đợi đăng nhập VnEdu (90s).\n\n"
            "Hãy đăng nhập VnEdu trong cửa sổ Chrome vừa mở, "
            "vào module Kế hoạch dạy học, rồi bấm [Đăng nhập VnEdu] lại.",
        ))
        return None

    @staticmethod
    def _find_vnedu_page(browser):
        """Tìm tab VnEdu /v5/ trong các context."""
        for ctx in browser.contexts:
            for p in ctx.pages:
                url = (p.url or "").lower()
                if "vnedu" in url and "/v5/" in url:
                    return p
        # Fallback: bất kỳ tab vnedu nào
        for ctx in browser.contexts:
            for p in ctx.pages:
                url = (p.url or "").lower()
                if "vnedu" in url:
                    return p
        return None

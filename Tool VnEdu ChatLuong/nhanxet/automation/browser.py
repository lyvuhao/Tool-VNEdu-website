"""Khởi động/kết nối Chrome debug và chọn tab VNEDU."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, List, Tuple
from urllib.parse import urlparse
from urllib.request import urlopen

from playwright.sync_api import Error as PlaywrightError, Page, sync_playwright


class BrowserMixin:
    """Khởi động/kết nối Chrome debug và chọn tab VNEDU."""

    def _cdp_http_endpoint(self) -> str:
        """Returns the local HTTP endpoint exposed by Chromium DevTools."""
        return f"http://127.0.0.1:{self.debug_port}"

    def _is_cdp_ready(self) -> bool:
        """Checks whether a valid Chromium CDP endpoint is reachable."""
        try:
            with urlopen(f"{self._cdp_http_endpoint()}/json/version", timeout=2.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError):
            return False

        browser_name = str(payload.get("Browser", "")).lower()
        return bool(payload.get("webSocketDebuggerUrl")) and (
            "chrome" in browser_name or "chromium" in browser_name or "edg" in browser_name
        )

    def _find_chromium_executable(self) -> str:
        """Tries common Windows locations for Chrome or Edge."""
        candidates = [
            Path(os.environ.get("PROGRAMFILES", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        ]

        for command in ("chrome", "msedge", "chromium"):
            located = shutil.which(command)
            if located:
                candidates.append(Path(located))

        for candidate in candidates:
            if candidate and candidate.exists() and candidate.is_file():
                return str(candidate)
        return ""

    def _start_debug_browser(self) -> None:
        """Starts a dedicated Chromium session with remote debugging enabled."""
        executable = self._find_chromium_executable()
        if not executable:
            raise RuntimeError("Không tìm thấy Chrome/Edge trên máy để mở phiên debug.")

        self._cdp_profile_dir.mkdir(parents=True, exist_ok=True)
        args = [
            executable,
            f"--remote-debugging-port={self.debug_port}",
            f"--user-data-dir={self._cdp_profile_dir}",
            "--new-window",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        if self.target_url:
            args.append(self.target_url)

        try:
            subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
        except OSError as error:
            raise RuntimeError(f"Không thể khởi động browser debug: {error}") from error

    def _ensure_cdp_server(self) -> None:
        """Ensures the CDP endpoint exists before any automation action."""
        if self._is_cdp_ready():
            return

        self._start_debug_browser()
        deadline = time.time() + 20
        while time.time() < deadline:
            if self._is_cdp_ready():
                return
            time.sleep(0.5)
        raise RuntimeError(
            f"Không thể mở cổng debug {self.debug_port}. Browser debug chưa sẵn sàng."
        )

    def _is_internal_browser_url(self, raw_url: str) -> bool:
        """Returns whether one CDP page URL points to a browser-internal surface."""
        url = raw_url.strip().lower()
        return (
            not url
            or url.startswith("chrome://")
            or url.startswith("chrome-extension://")
            or url.startswith("devtools://")
            or url.startswith("edge://")
            or url.startswith("about:")
        )

    def _target_host(self) -> str:
        """Returns the configured VNEDU host name when the target URL is valid."""
        try:
            return (urlparse(self.target_url).hostname or "").strip().lower()
        except ValueError:
            return ""

    def _page_priority(self, page: Page) -> int:
        """Scores CDP pages so the automation prefers stable VNEDU tabs over transient browser popups."""
        if page.is_closed():
            return -1

        page_url = (page.url or "").strip()
        if self._is_internal_browser_url(page_url):
            return 0

        parsed_page = urlparse(page_url)
        if parsed_page.scheme not in {"http", "https"}:
            return 1

        target_url = self.target_url.strip().lower()
        page_url_lower = page_url.lower()
        target_host = self._target_host()
        page_host = (parsed_page.hostname or "").strip().lower()

        if target_url and target_url in page_url_lower:
            return 110
        if target_host and page_host == target_host:
            return 100
        if page_host == "user.vnedu.vn" and parsed_page.path.startswith("/sso"):
            return 95
        if "vnedu" in page_host:
            return 85
        return 50

    def _pick_target_page(self, contexts) -> Page | None:
        """Selects the most suitable page from the live CDP session."""
        scored_pages: List[Tuple[int, int, Page]] = []
        ordinal = 0
        for context in contexts:
            for page in context.pages:
                try:
                    score = self._page_priority(page)
                except PlaywrightError:
                    continue
                if score >= 0:
                    scored_pages.append((score, ordinal, page))
                ordinal += 1
        if not scored_pages:
            return None
        scored_pages.sort(key=lambda item: (item[0], item[1]), reverse=True)
        best_score, _best_ordinal, best_page = scored_pages[0]
        if best_score >= 85:
            return best_page
        return None

    @contextmanager
    def _open_page(self) -> Iterator[Page]:
        """Connects to the live Chromium instance and yields one reusable working page."""
        self._ensure_cdp_server()
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.connect_over_cdp(self._cdp_http_endpoint())
            except PlaywrightError as error:
                raise RuntimeError(
                    f"Kết nối CDP thất bại tại {self._cdp_http_endpoint()}."
                ) from error

            try:
                contexts = browser.contexts
                if not contexts:
                    raise RuntimeError("Không tìm thấy browser context trong phiên CDP.")

                target_page = self._pick_target_page(contexts)
                if target_page is None:
                    target_page = contexts[0].new_page()
                yield target_page
            finally:
                browser.close()

    def _goto_target_page(self, page: Page) -> None:
        """Navigates to the configured VNEDU URL when the current page differs."""
        if self.target_url and self.target_url not in (page.url or ""):
            page.goto(self.target_url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(300)

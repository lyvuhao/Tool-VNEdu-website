"""Kiểm tra cổng CDP và liệt kê tab Chrome."""

import json
import urllib.error
import urllib.request

from .config import CDP_HOST, CHROME_DEBUG_PROFILE_DIR, DEFAULT_CDP_PORT


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

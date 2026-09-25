"""Thư viện tuỳ chọn: winsound (Windows) và Playwright."""


try:
    import winsound
except ImportError:
    winsound = None


try:
    from playwright.sync_api import sync_playwright
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

__all__ = ["winsound", "sync_playwright", "PlaywrightTimeout", "HAS_PLAYWRIGHT"]

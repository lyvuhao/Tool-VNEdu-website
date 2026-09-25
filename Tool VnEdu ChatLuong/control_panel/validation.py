"""Kiểm tra URL và cổng debug."""

from __future__ import annotations

from urllib.parse import urlparse


def validate_target_url(raw_url: str) -> str:
    """Validate the VNEDU URL used by the automation session."""

    url = raw_url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("URL vnEdu phải bắt đầu bằng http:// hoặc https://.")
    if "vnedu" not in parsed.netloc.lower():
        raise ValueError("URL không giống tên miền vnEdu.")
    return url


def parse_debug_port(raw_port: str) -> int:
    """Parse and validate the local Chrome DevTools port."""

    try:
        port = int(str(raw_port).strip())
    except ValueError as error:
        raise ValueError("Cổng CDP phải là số.") from error
    if port < 1024 or port > 65535:
        raise ValueError("Cổng CDP phải nằm trong khoảng 1024..65535.")
    return port

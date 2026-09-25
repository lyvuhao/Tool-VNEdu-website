"""Tiện ích báo tiến độ (progress callback) dùng chung cho automation và GUI."""

from __future__ import annotations

from .config import ProgressCallback


def clamp_progress_value(value: float) -> float:
    """Clamps one progress value into the GUI-safe 0..100 range."""
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(100.0, numeric_value))


def emit_progress(
    progress_callback: ProgressCallback | None,
    value: float,
    message: str = "",
) -> None:
    """Sends one progress update when a reporter is available."""
    if progress_callback is None:
        return
    progress_callback(clamp_progress_value(value), str(message or "").strip())


def create_subprogress_reporter(
    progress_callback: ProgressCallback | None,
    start_value: float,
    end_value: float,
) -> ProgressCallback | None:
    """Maps one nested 0..100 reporter into a parent progress span."""
    if progress_callback is None:
        return None

    clamped_start = clamp_progress_value(start_value)
    clamped_end = clamp_progress_value(end_value)
    span = clamped_end - clamped_start

    def report(value: float, message: str = "") -> None:
        nested_value = clamp_progress_value(value)
        progress_callback(clamped_start + (span * (nested_value / 100.0)), message)

    return report


def build_progress_caption(progress_value: float, message: str, max_message_length: int = 48) -> str:
    """Builds a compact progress caption suitable for the canvas-based progress bar."""
    normalized_value = int(round(clamp_progress_value(progress_value)))
    normalized_message = str(message or "").strip() or "Sẵn sàng"
    if len(normalized_message) > max_message_length:
        normalized_message = normalized_message[: max_message_length - 3].rstrip() + "..."
    return f"{normalized_value:>3}% | {normalized_message}"


def password_entry_show_value(show_password: bool) -> str:
    """Returns the Tk `show` value for the password entry."""
    return "" if show_password else "*"

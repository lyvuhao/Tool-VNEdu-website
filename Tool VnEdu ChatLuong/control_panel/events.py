"""Event bus nội bộ giữa worker và giao diện."""

from __future__ import annotations

import tkinter as tk
from typing import Callable

from .log import _get_logger


class _EventBus:
    """Thread-safe in-process pub/sub for dashboard state changes."""

    def __init__(self, root: tk.Tk) -> None:
        self._root = root
        self._subs: dict[str, list[Callable[..., None]]] = {}

    def on(self, event: str, handler: Callable[..., None]) -> None:
        """Subscribe a handler to an event name."""
        self._subs.setdefault(event, []).append(handler)

    def off(self, event: str, handler: Callable[..., None]) -> None:
        """Unsubscribe a handler from an event name."""
        handlers = self._subs.get(event, [])
        if handler in handlers:
            handlers.remove(handler)

    def emit(self, event: str, **kwargs: object) -> None:
        """Thread-safe: schedules dispatch on Tk main thread."""
        try:
            self._root.after(0, lambda: self._dispatch(event, kwargs))
        except (RuntimeError, tk.TclError):
            pass

    def _dispatch(self, event: str, kwargs: dict[str, object]) -> None:
        for handler in self._subs.get(event, []):
            try:
                handler(**kwargs)
            except Exception:  # noqa: BLE001 - event handlers must not crash the dispatcher.
                _get_logger().error("Event handler error: %s", event, exc_info=True)

"""Lớp VnEduScoreAutomation: điều khiển Chrome (CDP) để đọc/ghi Sổ điểm VNEDU."""

from __future__ import annotations

import os
from pathlib import Path

from .access_scan import AccessScanMixin
from .browser import BrowserMixin
from .combo import ComboMixin
from .comment_write import CommentWriteMixin
from .context import ContextBuildMixin
from .context_select import ContextSelectMixin
from .login import LoginMixin
from .schema import SchemaMixin
from .snapshot import SnapshotMixin


class VnEduScoreAutomation(
    BrowserMixin,
    LoginMixin,
    SnapshotMixin,
    ComboMixin,
    ContextBuildMixin,
    SchemaMixin,
    CommentWriteMixin,
    AccessScanMixin,
    ContextSelectMixin,
):
    """Playwright CDP automation for the VNEDU scorebook screen."""

    def __init__(self, debug_port: int, target_url: str) -> None:
        self.debug_port = int(debug_port)
        self.target_url = target_url.strip()
        local_app_data = os.environ.get("LOCALAPPDATA", str(Path.home()))
        self._cdp_profile_dir = Path(local_app_data) / "VNEDU-NHANXET-V2-CDP"

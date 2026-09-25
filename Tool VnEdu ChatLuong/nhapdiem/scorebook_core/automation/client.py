"""VnEduScoreAutomation của tool Nhập điểm: kế thừa bản `nhanxet`, ghi đè/bổ sung phần đọc & ghi điểm."""

from __future__ import annotations

from nhanxet.automation.client import VnEduScoreAutomation as _NhanXetScoreAutomation

from .context_select import ContextSelectMixin
from .payload_write import PayloadWriteMixin
from .score_scan import ScoreScanMixin
from .snapshot import ScoreSnapshotMixin


class VnEduScoreAutomation(
    ScoreSnapshotMixin,
    ScoreScanMixin,
    PayloadWriteMixin,
    ContextSelectMixin,
    _NhanXetScoreAutomation,
):
    """Playwright CDP automation for the VNEDU scorebook screen."""

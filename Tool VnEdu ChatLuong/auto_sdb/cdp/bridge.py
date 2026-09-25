"""ChromeBridge — kết nối Chrome đang mở qua CDP và điều khiển trang Sổ đầu bài VnEdu."""

from .config import DEFAULT_CDP_PORT, DEFAULT_TIMEOUT_MS
from .connection import ConnectionMixin
from .delete import DeleteMixin
from .dropdowns import DropdownMixin
from .entry_flow import EntryFlowMixin
from .form_fill import FormFillMixin
from .form_save import FormSaveMixin
from .form_wait import FormWaitMixin
from .khdh import KHDHScheduleMixin
from .lesson_form import LessonFormMixin
from .navigation import NavigationMixin
from .page_utils import PageUtilsMixin
from .sodaubai_fetch import SoDauBaiFetchMixin
from .stats import StatsMixin
from .table import TableMixin


class ChromeBridge(
    ConnectionMixin,
    NavigationMixin,
    DropdownMixin,
    StatsMixin,
    TableMixin,
    KHDHScheduleMixin,
    DeleteMixin,
    LessonFormMixin,
    SoDauBaiFetchMixin,
    FormWaitMixin,
    FormFillMixin,
    FormSaveMixin,
    EntryFlowMixin,
    PageUtilsMixin,
):
    """Kết nối Chrome qua CDP, đọc/điều khiển DOM VnEdu sổ đầu bài.

    QUAN TRỌNG: Tạo và sử dụng instance trong CÙNG MỘT THREAD.
    Playwright sync_api không thread-safe — tất cả method phải gọi
    từ thread đã gọi connect().

    Lifecycle:
        bridge = ChromeBridge(port=9224)
        ok, msg = bridge.connect()
        if ok:
            bridge.select_tuan("Tuần 25")
            bridge.select_lop("6A1")
            table = bridge.read_table()
            for row in table:
                bridge.type_one_entry(row, ppct=..., ...)
        bridge.disconnect()
    """

    def __init__(self, port=DEFAULT_CDP_PORT, timeout_ms=DEFAULT_TIMEOUT_MS):
        """Khởi tạo ChromeBridge.

        Args:
            port: CDP port (mặc định DEFAULT_CDP_PORT = 9224)
            timeout_ms: Timeout mặc định cho thao tác DOM (ms)
        """
        self.port = port
        self.timeout_ms = timeout_ms
        self._pw = None
        self.browser = None
        self.page = None
        self._connected = False

        # Cache selectors đã discover (tránh inspect lại mỗi lần)
        self._cached_selectors = {}

        # Stop signal cho batch operations
        self._stop_requested = False

        # Reference của dialog handler đang đăng ký (để gỡ idempotent)
        self._dialog_handler = None

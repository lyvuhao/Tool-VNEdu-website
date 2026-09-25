"""Cấu hình kết nối Chrome DevTools Protocol và URL VnEdu."""

import logging
import os as _os


logger = logging.getLogger("chrome_bridge")


# --- Hằng số mặc định ---
DEFAULT_CDP_PORT = 9224


CDP_HOST = "127.0.0.1"


DEFAULT_TIMEOUT_MS = 10000       # 10s cho thao tác thông thường


NAV_TIMEOUT_MS = 30000           # 30s cho navigation (chọn dropdown → page reload)


FORM_WAIT_MS = 8000              # 8s chờ form popup mở


POST_SELECT_DELAY = 1.5          # Delay (s) sau khi chọn dropdown, chờ AJAX/reload


POST_CLICK_DELAY = 0.15          # Delay (s) dự phòng sau khi click nút ➕


POST_FILL_DELAY = 0.3            # Delay (s) sau khi fill mỗi field


POST_SAVE_DELAY = 1.5            # Delay (s) sau khi save, chờ dialog đóng


# URL pattern nhận diện trang VnEdu
VNEDU_URL_PATTERNS = ["vnedu.vn", "vnedu."]


CHROME_DEBUG_PROFILE_DIR = _os.path.join(
    _os.environ.get("USERPROFILE", "C:\\Users\\Default"),
    "chrome-debug-profile"
)


# Lệnh mở Chrome với CDP (bao gồm --user-data-dir riêng)
CHROME_LAUNCH_CMD = (
    'start chrome.exe --remote-debugging-port={port} '
    '--user-data-dir="{profile_dir}"'
)


VNEDU_SSO_LOGIN_URL = (
    "https://user.vnedu.vn/sso/?app_id=1&use_cache=1&continue="
    "http://diendan.vnedu.vn/security/ssoVnedu"
)


VNEDU_HOME_URL = "https://vnedu.vn/"

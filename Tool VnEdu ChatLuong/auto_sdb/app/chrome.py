"""Mở Chrome debug và kết nối CDP."""

import threading
import time

from ..cdp.config import (
    CHROME_DEBUG_PROFILE_DIR,
    CHROME_LAUNCH_CMD,
    DEFAULT_CDP_PORT,
    VNEDU_SSO_LOGIN_URL,
)
from ..cdp.health import cdp_health_check
from ..config import _HAS_CDP


class ChromeConnectionMixin:
    """Mở Chrome debug và kết nối CDP."""

    # -----------------------------------------------------------------
    # CDP CONNECTION LOGIC
    # -----------------------------------------------------------------

    def _find_chrome_exe(self):
        """Tìm đường dẫn chrome.exe trên hệ thống.

        Returns:
            str hoặc None: Đường dẫn đầy đủ tới chrome.exe nếu tìm thấy.
        """
        import os
        candidates = [
            os.path.join(os.environ.get("ProgramFiles", ""), "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
        ]
        for path in candidates:
            if path and os.path.isfile(path):
                return path
        return None

    def _get_cdp_port(self):
        """Trả về CDP port cố định của app."""
        self.var_cdp_port.set(DEFAULT_CDP_PORT)
        return DEFAULT_CDP_PORT

    def _list_debug_profile_chrome_pids(self):
        """Liệt kê PID chrome.exe đang dùng user-data-dir debug riêng của app."""
        import subprocess
        import json
        import os

        profile_dir = os.path.normcase(os.path.normpath(CHROME_DEBUG_PROFILE_DIR)).replace("/", "\\")
        ps_script = (
            "$procs = Get-CimInstance Win32_Process -Filter \"name = 'chrome.exe'\" | "
            "Select-Object ProcessId, CommandLine; "
            "$procs | ConvertTo-Json -Compress"
        )
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if proc.returncode != 0:
                return []
            raw = (proc.stdout or "").strip()
            if not raw:
                return []
            data = json.loads(raw)
            if isinstance(data, dict):
                data = [data]
            pids = []
            for item in data:
                cmd = str(item.get("CommandLine") or "")
                if not cmd:
                    continue
                cmd_norm = os.path.normcase(cmd).replace("/", "\\")
                if profile_dir in cmd_norm:
                    try:
                        pid = int(item.get("ProcessId") or 0)
                    except (TypeError, ValueError):
                        pid = 0
                    if pid > 0:
                        pids.append(pid)
            return sorted(set(pids))
        except Exception:
            return []

    def _terminate_debug_profile_chrome(self, pids):
        """Chỉ đóng các process Chrome debug riêng của app, không ảnh hưởng Chrome thường."""
        import subprocess
        import time

        terminated = 0
        for pid in list(pids or []):
            try:
                proc = subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if int(getattr(proc, "returncode", 1)) == 0:
                    terminated += 1
                else:
                    stderr_text = str(getattr(proc, "stderr", "") or "").strip()
                    if stderr_text:
                        self._log(
                            f"Không thể đóng Chrome PID {pid}: {stderr_text[:180]}",
                            "warning",
                        )
            except Exception:
                continue
        if terminated:
            time.sleep(0.8)
        return terminated

    def _launch_or_reuse_chrome_auto_sync(self):
        """Đảm bảo Chrome Auto trên port cố định đã sẵn sàng."""
        import subprocess
        import os

        port = self._get_cdp_port()
        ok, info = cdp_health_check(port)
        if ok:
            return True, info, port

        chrome_path = self._find_chrome_exe()
        if not chrome_path:
            return False, "Không tìm thấy chrome.exe trên hệ thống!", port

        debug_pids = self._list_debug_profile_chrome_pids()
        if debug_pids:
            self._terminate_debug_profile_chrome(debug_pids)

        os.makedirs(CHROME_DEBUG_PROFILE_DIR, exist_ok=True)
        subprocess.Popen(
            [
                chrome_path,
                f"--remote-debugging-port={port}",
                f"--user-data-dir={CHROME_DEBUG_PROFILE_DIR}",
                "--new-window",
                VNEDU_SSO_LOGIN_URL,
            ],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )

        for _ in range(30):
            time.sleep(0.5)
            ok, info = cdp_health_check(port)
            if ok:
                return True, info, port
        return False, f"Chrome đã mở nhưng port {port} chưa sẵn sàng sau 15s.", port

    def _on_launch_chrome_debug(self):
        """Mở Chrome Debug riêng của app với --remote-debugging-port.

        Quy trình:
        1. Tìm chrome.exe trên hệ thống
        2. Kiểm tra port đã listening chưa (nếu rồi thì kết nối luôn)
        3. Nếu có Chrome debug riêng của app đang treo: chỉ đóng instance đó
        4. Mở lại Chrome với debug flag + user-data-dir riêng
        5. Chờ port sẵn sàng → tự động kết nối
        """
        port = self._get_cdp_port()

        # Bước 1: Kiểm tra port đã listening chưa
        ok, info = cdp_health_check(port)
        if ok:
            self._log(f"Chrome debug port {port} đã sẵn sàng, kết nối...", "info")
            self._cdp_connect_result(True, info, port)
            return

        # Bước 2: Tìm chrome.exe
        chrome_path = self._find_chrome_exe()
        if not chrome_path:
            self._log("Không tìm thấy chrome.exe trên hệ thống!", "error")
            self.lbl_cdp_status.config(
                text="Không tìm thấy Chrome. Cài Chrome hoặc dùng Sao chép lệnh.",
                foreground="red"
            )
            return

        self.lbl_cdp_status.config(text="⏳ Đang khởi động Chrome Debug...", foreground="orange")
        self.btn_launch_chrome.config(state="disabled")
        self.root.update_idletasks()

        def _launch_work():
            """Thread worker: chỉ dọn Chrome debug riêng của app → mở mới → chờ port."""
            try:
                ok_launch, info_launch, used_port = self._launch_or_reuse_chrome_auto_sync()
                self._post_ui(lambda: self._chrome_launch_result(ok_launch, info_launch, used_port))
            except Exception as e:
                self._post_ui(lambda e=e: self._chrome_launch_result(
                    False, f"Lỗi khởi động Chrome: {e}", port
                ))

        threading.Thread(target=_launch_work, daemon=True).start()

    def _chrome_launch_result(self, ok, info, port):
        """Callback sau khi Chrome debug được khởi động (UI thread).

        Args:
            ok: True nếu debug port đã sẵn sàng.
            info: Thông tin Chrome hoặc thông báo lỗi.
            port: Số port CDP.
        """
        self.btn_launch_chrome.config(state="normal")
        if ok:
            self._log(f"Chrome Debug đã sẵn sàng trên port {port}", "success")
            self._cdp_connect_result(True, info, port)
        else:
            self.lbl_cdp_status.config(text=f"✗ {info}", foreground="red")
            self._log(f"Launch Chrome failed: {info}", "error")

    def _copy_chrome_cmd(self):
        """Copy lệnh mở Chrome với CDP vào clipboard."""
        port = self._get_cdp_port()
        cmd = CHROME_LAUNCH_CMD.format(port=port, profile_dir=CHROME_DEBUG_PROFILE_DIR)
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(cmd)
            self.root.update_idletasks()
            self._log(f"Đã copy: {cmd}", "info")
        except Exception:
            self._log(f"Lệnh: {cmd}", "info")

    def _on_cdp_connect(self):
        """Kết nối Chrome CDP (chạy health check trong thread)."""
        if not _HAS_CDP:
            self._log("Module chrome_bridge.py không tìm thấy!", "error")
            return

        self.lbl_cdp_status.config(text="⏳ Đang kết nối...", foreground="orange")
        self.btn_cdp_connect.config(state="disabled")
        self.root.update_idletasks()

        port = self._get_cdp_port()

        def _check():
            ok, info = cdp_health_check(port)
            self._post_ui(lambda: self._cdp_connect_result(ok, info, port))

        threading.Thread(target=_check, daemon=True).start()

    def _cdp_connect_result(self, ok, info, port):
        """Callback sau health check (chạy trong UI thread)."""
        if ok:
            self._cdp_connected = True
            self._cdp_port = port
            self._mark_sched_form_session_changed(
                clear_cache=True,
                reason=(
                    "CDP vừa kết nối hoặc đổi session. Hãy bấm [Quét Form] lại "
                    "để nạp đúng Môn học / Phân môn trước khi chạy schedule."
                ),
                announce=True,
                log_level="info",
            )
            self._sched_teacher_progress_latest_week = None
            self.lbl_cdp_status.config(
                text=f"● Đã kết nối: {info}", foreground="green"
            )
            self.btn_cdp_connect.config(state="disabled")
            self.btn_cdp_disconnect.config(state="normal")
            self.btn_inspect.config(state="normal")
            self.btn_recover_web.config(state="normal")
            self.btn_discover_delete.config(state="normal")
            self.btn_sched_load_lop.config(state="normal")
            self._set_schedule_button_states(
                running=False,
                can_resume=bool(self._schedule_resume_state),
            )
            self._set_quick_prepare_button_state()
            self._set_auto_login_button_state()
            self._log(f"CDP Connected: {info}", "success")

            # Auto-load thông tin Tuần/Lớp hiện tại
            self._on_load_current_info()
            if self.var_sched_lop.get().strip():
                self._on_sched_progress_context_changed()
        else:
            self._cdp_connected = False
            self.lbl_cdp_status.config(
                text=f"✗ {info}", foreground="red"
            )
            self.btn_cdp_connect.config(state="normal")
            self.btn_recover_web.config(state="disabled")
            self._set_schedule_button_states(
                running=False,
                can_resume=bool(self._schedule_resume_state),
            )
            self._set_quick_prepare_button_state()
            self._set_auto_login_button_state()
            self._log(f"CDP Connect failed: {info}", "error")

    def _on_cdp_disconnect(self):
        """Ngắt kết nối CDP."""
        self._cdp_connected = False
        self._cancel_sched_teacher_progress_jobs()
        self._sched_teacher_progress_request_id += 1
        self._mark_sched_form_session_changed(
            clear_cache=True,
            reason=(
                "CDP đã ngắt kết nối. Khi kết nối lại, hãy bấm [Quét Form] trước "
                "khi chạy hoặc tiếp tục schedule."
            ),
        )
        self._sched_teacher_progress_latest_week = None
        self.lbl_cdp_status.config(text="○ Đã ngắt kết nối", foreground="gray")
        self.btn_cdp_connect.config(state="normal")
        self.btn_cdp_disconnect.config(state="disabled")
        self.btn_inspect.config(state="disabled")
        self.btn_recover_web.config(state="disabled")
        self.btn_discover_delete.config(state="disabled")
        self.btn_sched_load_lop.config(state="disabled")
        self.btn_sched_run.config(state="disabled")
        self.btn_sched_stop.config(state="disabled")
        self.btn_sched_resume.config(state="disabled")
        self._set_quick_prepare_button_state()
        self._set_auto_login_button_state()
        self.lbl_cdp_info.config(text="")
        self._render_sched_teacher_progress(None)
        self._log("CDP Disconnected", "info")

"""Kết nối Chrome và nạp dữ liệu khởi tạo."""

from __future__ import annotations

import queue
from tkinter import messagebox

from ..workers.login import LoginWorker


class ConnectMixin:
    """Kết nối Chrome và nạp dữ liệu khởi tạo."""

    # -----------------------------------------------------------
    # Connect Chrome
    # -----------------------------------------------------------

    def _on_connect_clicked(self):
        if self._guard_cdp_exclusive("Đăng nhập VnEdu"):
            return

        # Validate tài khoản + mật khẩu
        username = self.var_username.get().strip()
        password = self.var_password.get().strip()
        if not username:
            messagebox.showwarning(
                "Thiếu thông tin", "Hãy nhập Tài khoản VnEdu.", parent=self
            )
            self._entry_username.focus_set()
            return
        if not password:
            messagebox.showwarning(
                "Thiếu thông tin", "Hãy nhập Mật khẩu.", parent=self
            )
            self._entry_password.focus_set()
            return

        # Spawn LoginWorker
        self._bootstrap_queue = queue.Queue()
        self._bootstrap_worker = LoginWorker(
            username=username,
            password=password,
            port=int(self.var_port.get()),
            event_queue=self._bootstrap_queue,
        )
        self._bootstrap_worker.start()
        self.btn_connect.configure(state="disabled")
        self.var_connect_status.set("⏳ Đang đăng nhập…")
        self._log("━━ Đăng nhập VnEdu ━━", "info")
        self._poll_bootstrap_queue()

    def _poll_bootstrap_queue(self):
        try:
            while True:
                ev = self._bootstrap_queue.get_nowait()
                kind = ev[0]
                if kind == "status":
                    self.var_status.set(ev[1])
                    self._log(ev[1], "info")
                elif kind == "ctx":
                    ctx = ev[1]
                    self.ctx_info = ctx
                    self.var_user_info.set(
                        f"●  {ctx.giao_vien_name}    •    "
                        f"Năm học {ctx.nam_hoc}–{ctx.nam_hoc + 1}    •    "
                        f"Cấp {ctx.cap_hoc_text}"
                    )
                    # Cập nhật profile với thông tin GV
                    if (self.profile.ho_ten_gv == "(chưa nhập)"
                            or not self.profile.ho_ten_gv.strip()):
                        name = ctx.giao_vien_name.split(" - ")[0].strip()
                        self.profile.ho_ten_gv = name
                        self.profile.nam_hoc = ctx.nam_hoc
                        self.profile.cap_hoc = ctx.cap_hoc
                        self.profile.cap_hoc_text = ctx.cap_hoc_text
                        self.profile.ma_truong = str(ctx.site_id or "")
                elif kind == "done":
                    self.bootstrap_data = ev[1]
                    self.var_connect_status.set(
                        f"✓ Đã đăng nhập — "
                        f"{len(self.bootstrap_data.lop_options)} lớp khả dụng"
                    )
                    self.var_summary.set(
                        f"●  Sẵn sàng. {len(self.bootstrap_data.lop_options)} lớp, "
                        f"{sum(len(v) for v in self.bootstrap_data.mon_by_lop.values())} môn. "
                        "Click ô bất kỳ trong lưới để bắt đầu soạn lịch."
                    )
                    self._log(
                        f"Đã có {len(self.bootstrap_data.lop_options)} lớp.",
                        "ok",
                    )
                    # (#4) Login thành công → xóa mật khẩu khỏi field + var.
                    # Defense-in-depth: không giữ plaintext trong UI sau khi
                    # đã submit. Tài khoản giữ lại cho lần đăng nhập lại.
                    try:
                        self.var_password.set("")
                        self._entry_password.delete(0, "end")
                    except Exception:
                        pass
                elif kind == "login_failed":
                    # Sai tk/mk — focus lại field mật khẩu để user nhập lại
                    self.var_connect_status.set("❌ Sai tài khoản hoặc mật khẩu")
                    self._log(ev[1], "err")
                    try:
                        self._entry_password.delete(0, "end")
                        self._entry_password.focus_set()
                    except Exception:
                        pass
                elif kind == "error":
                    self.var_connect_status.set("❌ Đăng nhập thất bại")
                    self._log(ev[1], "err")
                    messagebox.showerror("Lỗi đăng nhập", ev[1], parent=self)
        except queue.Empty:
            pass

        if self._bootstrap_worker and self._bootstrap_worker.is_alive():
            self._safe_after(100, self._poll_bootstrap_queue)
        else:
            self.btn_connect.configure(state="normal")

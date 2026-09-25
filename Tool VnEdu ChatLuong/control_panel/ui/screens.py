"""Màn hình đăng nhập, dashboard và luồng đăng nhập."""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk
from urllib.parse import urlparse

from ..config import PANEL_COLOR, SURFACE_COLOR, UI_FONT_FAMILY
from ..custom_tools import load_custom_tools
from ..log import _get_logger
from ..login import login_to_vnedu
from ..tool_configs import sync_tool_configs
from ..tool_registry import TOOL_FILES
from ..validation import parse_debug_port, validate_target_url


class ScreensMixin:
    """Màn hình đăng nhập, dashboard và luồng đăng nhập."""

    def _build_login_screen(self) -> None:
        """Build the first screen with only VNEDU login controls."""

        self._clear_root()
        login_width = 455
        login_height = 352
        login_x = max((self.root.winfo_screenwidth() - login_width) // 2, 0)
        login_y = max((self.root.winfo_screenheight() - login_height) // 2, 0)
        self.root.geometry(f"{login_width}x{login_height}+{login_x}+{login_y}")
        self.root.minsize(445, 342)
        self.root.resizable(False, False)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        shell = ttk.Frame(self.root, style="App.TFrame", padding=(20, 15, 20, 16))
        shell.grid(row=0, column=0, sticky="nsew")
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(2, weight=0)

        login_header = ttk.Frame(shell, style="App.TFrame")
        login_header.grid(row=0, column=0, sticky="ew", pady=(0, 2))
        login_header.columnconfigure(0, weight=1)
        self._login_title_canvas(login_header).grid(row=0, column=0, sticky="")
        subtitle = ttk.Frame(login_header, style="App.TFrame")
        subtitle.grid(row=1, column=0, sticky="", pady=(2, 0))
        self._lock_icon(subtitle).grid(row=0, column=0, sticky="e", padx=(0, 5))
        tk.Label(
            subtitle,
            text="MẬT KHẨU KHÔNG ĐƯỢC LƯU",
            bg=SURFACE_COLOR,
            fg="#48627f",
            font=(UI_FONT_FAMILY, 9, "bold"),
            anchor="center",
            justify="center",
        ).grid(row=0, column=1, sticky="w")
        tk.Frame(login_header, width=92, height=2, bg="#c7dbff").grid(row=2, column=0, pady=(6, 0))

        panel, frame = self._outlined_panel(shell, padding=(18, 13, 18, 15))
        panel.grid(row=2, column=0, sticky="w", pady=(10, 0))
        frame.columnconfigure(0, weight=1)

        ttk.Label(frame, text="THÔNG TIN ĐĂNG NHẬP", style="PanelTitle.TLabel", font=(UI_FONT_FAMILY, 10, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 10)
        )

        form = ttk.Frame(frame, style="Panel.TFrame")
        form.grid(row=1, column=0, sticky="w")
        form.columnconfigure(0, minsize=92)

        ttk.Label(form, text="URL VNEDU", style="DialogFieldTitle.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 14), pady=(0, 8)
        )
        ttk.Entry(form, textvariable=self.url_var, width=39).grid(row=0, column=1, sticky="w", pady=(0, 8))

        ttk.Label(form, text="CDP", style="DialogFieldTitle.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 14), pady=(0, 8)
        )
        ttk.Entry(form, textvariable=self.port_var, width=10).grid(row=1, column=1, sticky="w", pady=(0, 8))

        ttk.Label(form, text="TÀI KHOẢN", style="DialogFieldTitle.TLabel").grid(
            row=2, column=0, sticky="w", padx=(0, 14), pady=(0, 8)
        )
        self.username_entry = ttk.Entry(form, textvariable=self.username_var, width=32)
        self.username_entry.grid(row=2, column=1, sticky="w", pady=(0, 8))

        ttk.Label(form, text="MẬT KHẨU", style="DialogFieldTitle.TLabel").grid(
            row=3, column=0, sticky="w", padx=(0, 14)
        )
        password_row = ttk.Frame(form, style="Panel.TFrame")
        password_row.grid(row=3, column=1, sticky="w")
        self.password_entry = ttk.Entry(password_row, textvariable=self.password_var, show="*", width=32)
        self.password_entry.grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(
            password_row,
            text="Hiện",
            variable=self.show_password_var,
            command=self._toggle_password,
        ).grid(row=0, column=1, padx=(8, 0))

        action_row = ttk.Frame(frame, style="Panel.TFrame")
        action_row.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        action_row.columnconfigure(1, weight=1)
        status_dot = tk.Canvas(action_row, width=12, height=12, bg=PANEL_COLOR, highlightthickness=0)
        status_dot.create_oval(2, 2, 10, 10, fill="#f59e0b", outline="")
        status_dot.grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Label(action_row, textvariable=self.status_var, style="PanelStatus.TLabel", wraplength=260).grid(
            row=0, column=1, sticky="w"
        )
        self.login_button = self._flat_button(
            action_row,
            text="Đăng nhập",
            command=self._start_login,
            width=9,
            primary=True,
        )
        self.login_button.grid(row=0, column=2, sticky="e", padx=(12, 0))
        tk.Label(
            shell,
            text="Develop by Vu Hao",
            bg=SURFACE_COLOR,
            fg="#708093",
            font=(UI_FONT_FAMILY, 8, "bold"),
            anchor="center",
            justify="center",
        ).grid(row=3, column=0, sticky="ew", pady=(9, 0))
        self.root.bind("<Return>", lambda _event: self._start_login())
        self.username_entry.focus_set()

    def _build_dashboard_screen(self) -> None:
        """Build the dashboard after VNEDU login succeeds."""

        self._clear_root()
        self.root.unbind("<Return>")
        if self.advanced_mode_var.get():
            self.root.geometry("900x395+70+65")
            self.root.minsize(860, 390)
        else:
            self.root.geometry("900x370+70+65")
            self.root.minsize(860, 365)
        self.root.resizable(False, False)
        self.custom_tools = load_custom_tools()
        self._refresh_session_caption()
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=0)
        self.root.rowconfigure(1, weight=0)
        self.root.rowconfigure(2, weight=0)

        header = ttk.Frame(self.root, style="App.TFrame", padding=(22, 18, 22, 10))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(
            header,
            text="VNEDU Control Panel",
            style="DashboardTitle.TLabel",
            font=(UI_FONT_FAMILY, 18, "bold"),
        ).grid(row=0, column=0, sticky="w")
        header_actions = ttk.Frame(header, style="App.TFrame")
        header_actions.grid(row=0, column=1, rowspan=2, sticky="e", padx=(16, 0))
        self._flat_button(header_actions, text="Mở vnEdu", command=self._reopen_vnedu, width=8).grid(
            row=0, column=0, padx=(0, 8)
        )
        self._flat_button(header_actions, text="Thêm tool", command=self._open_add_tool_dialog, width=9).grid(
            row=0, column=1, padx=(0, 8)
        )
        self._flat_button(
            header_actions,
            text="Đổi file",
            command=self._open_replace_tool_dialog,
            width=8,
        ).grid(row=0, column=2, padx=(0, 8))
        tools_button = self._flat_button(
            header_actions,
            text="Công cụ",
            command=lambda: self._open_tools_menu(tools_button),
            width=7,
        )
        tools_button.grid(row=0, column=3)
        chip_frame = ttk.Frame(header, style="App.TFrame")
        chip_frame.grid(row=1, column=0, sticky="w")
        host = urlparse(self.url_var.get().strip()).netloc or self.url_var.get().strip()
        username = self.username_var.get().strip() or "(chưa nhập)"
        self._chip(chip_frame, f"URL  {host}", 0)
        self._chip(chip_frame, f"CDP  {self.port_var.get().strip()}", 1)
        self._chip(chip_frame, f"TK  {username}", 2)

        body = ttk.Frame(self.root, style="App.TFrame", padding=(22, 0, 22, 10))
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=0)

        cards_area, cards_frame = self._scrollable_tool_area(body)
        cards_area.grid(row=0, column=0, sticky="ew")
        self._dashboard_cards_frame = cards_frame
        self._running_tool_keys_snapshot = self._collect_running_tool_keys()
        self._render_dashboard_cards(cards_frame)
        self._start_tool_process_poll()

        footer = ttk.Frame(self.root, style="App.TFrame", padding=(22, 0, 22, 12))
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(1, weight=1)
        status_dot = tk.Canvas(footer, width=12, height=12, bg=SURFACE_COLOR, highlightthickness=0)
        status_dot.create_oval(2, 2, 10, 10, fill="#22c55e" if username != "(chưa nhập)" else "#f59e0b", outline="")
        status_dot.grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Label(
            footer,
            textvariable=self.smart_status_var,
            style="Status.TLabel",
            wraplength=620,
        ).grid(row=0, column=1, sticky="w")
        mode_text = "Nâng cao" if self.advanced_mode_var.get() else "Cơ bản"
        self._flat_button(footer, text=mode_text, command=self._toggle_dashboard_mode, compact=True, width=8).grid(
            row=0, column=2, sticky="e", padx=(14, 10)
        )
        ttk.Label(
            footer,
            text="Mật khẩu không được lưu.",
            style="Footer.TLabel",
        ).grid(row=0, column=3, sticky="e")
        self._schedule_dashboard_health_check()

        # --- Keyboard shortcuts ---
        self.root.bind("<Control-r>", lambda _: self._reopen_vnedu())
        self.root.bind("<Control-t>", lambda _: self._open_add_tool_dialog())
        self.root.bind("<Control-m>", lambda _: self._open_tool_manager_dialog())
        self.root.bind("<F5>", lambda _: self._schedule_dashboard_health_check(force=True))
        _get_logger().info("Dashboard built. Tools: %d default + %d custom.", len(TOOL_FILES), len(self.custom_tools))

    def _toggle_password(self) -> None:
        self.password_entry.configure(show="" if self.show_password_var.get() else "*")

    def _refresh_session_caption(self) -> None:
        username = self.username_var.get().strip() or "(chưa nhập tài khoản)"
        self.session_caption_var.set(f"{self.url_var.get().strip()}  |  CDP {self.port_var.get().strip()}  |  {username}")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        if hasattr(self, "login_button"):
            try:
                self.login_button.configure(state="disabled" if busy else "normal")
            except tk.TclError:
                pass

    def _mark_ui_preview(self) -> None:
        self.status_var.set("Đang xem dashboard. Các tool vẫn mở được, nhưng chưa có phiên đăng nhập chung.")

    def _start_login(self) -> None:
        if self._busy:
            return
        try:
            target_url = validate_target_url(self.url_var.get())
            debug_port = parse_debug_port(self.port_var.get())
        except ValueError as error:
            messagebox.showerror("Dữ liệu chưa hợp lệ", str(error), parent=self.root)
            return

        username = self.username_var.get().strip()
        password = self.password_var.get()
        if not username or not password:
            messagebox.showwarning("Thiếu thông tin", "Hãy nhập đủ tài khoản và mật khẩu VNEDU.", parent=self.root)
            return

        self._set_busy(True)
        self.status_var.set("Đang mở Chrome và đăng nhập VNEDU...")
        threading.Thread(
            target=self._login_worker,
            args=(username, password, target_url, debug_port),
            daemon=True,
            name="VNEDUDashboardLogin",
        ).start()

    def _login_worker(self, username: str, password: str, target_url: str, debug_port: int) -> None:
        try:
            message = login_to_vnedu(username, password, target_url, debug_port, self._progress)
        except Exception as error:  # noqa: BLE001 - user-facing login path.
            self.root.after(0, lambda error=error: self._login_failed(error))
            return
        self.root.after(0, lambda: self._login_done(username, target_url, debug_port, message))

    def _progress(self, _value: float, message: str = "") -> None:
        if message:
            self.root.after(0, lambda: self.status_var.set(message))

    def _login_failed(self, error: Exception) -> None:
        self._set_busy(False)
        detail = str(error).strip() or type(error).__name__
        self.status_var.set(f"Đăng nhập chưa hoàn tất: {detail}")
        messagebox.showerror("Không đăng nhập được", detail, parent=self.root)

    def _login_done(self, username: str, target_url: str, debug_port: int, message: str) -> None:
        self._set_busy(False)
        self.session.update(
            {
                "username": username,
                "target_url": target_url,
                "debug_port": debug_port,
                "message": message,
            }
        )
        self.password_var.set("")
        sync_tool_configs(username=username, target_url=target_url, debug_port=debug_port)
        self._refresh_session_caption()
        self.status_var.set(message or "Phiên VNEDU đã sẵn sàng.")
        self._build_dashboard_screen()

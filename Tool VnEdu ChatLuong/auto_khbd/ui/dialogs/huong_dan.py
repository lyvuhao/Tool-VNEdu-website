"""Hộp thoại hướng dẫn nhanh (4 trang)."""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk

from ...paths import TOOL_DIR
from ..styles import apply_wizard_styles
from ..theme import CLR_HINT, CLR_PANEL_BG


# =====================================================================
# Dialog — HuongDanDialog (4 trang hướng dẫn)
# =====================================================================

class HuongDanDialog(tk.Toplevel):
    """Cửa sổ Hướng dẫn sử dụng — 4 tab text Việt thuần + chỗ cho ảnh.

    Layout (theo spec khdh-wizard task 6.2):
      • ``ttk.Notebook`` 4 tab — mỗi tab 1 bước.
      • Mỗi tab: placeholder ảnh (load từ ``khdh_help/<image>`` nếu có,
        fallback ``Label`` text khi chưa có ảnh) + 3-5 dòng giải thích.
      • Toolbar dưới có nút "⬅ Quay lại" / "Tiếp ➡" để chuyển tab tuần tự
        + nút "Đóng" (close X = về main, không bắt buộc đóng).
      • Modal (``transient``) nhưng KHÔNG ``grab_set`` để giáo viên vẫn
        thao tác được trên màn chính nếu cần đối chiếu (Requirement 5.4).
    """

    PAGES = [
        {
            "title": "Bước 1 — Mở Chrome và đăng nhập VnEdu",
            "image": "buoc1_chrome.png",
            "body": (
                "1. Mở Chrome ở chế độ debug bằng cú pháp:\n"
                "   chrome.exe --remote-debugging-port=9224\n\n"
                "2. Đăng nhập VnEdu bằng tài khoản giáo viên của bạn.\n\n"
                "3. Vào module Kế hoạch dạy học (KHDH).\n\n"
                "4. Quay lại tool, bấm nút \"Đăng nhập VnEdu\". "
                "Công cụ sẽ tự đọc thông tin tài khoản và danh sách lớp/môn của bạn."
            ),
        },
        {
            "title": "Bước 2 — Soạn 1 mẫu lịch dạy",
            "image": "buoc2_soan_tkb.png",
            "body": (
                "Trên lưới Thứ 2 đến CN, bạn DOUBLE-CLICK vào ô tiết cần "
                "dạy (ví dụ Thứ 2 – Sáng – Tiết 1) để chọn:\n"
                "   • Lớp\n"
                "   • Môn học\n"
                "   • Phân môn\n\n"
                "Mẹo nhanh khi soạn nhiều tiết giống nhau:\n"
                "   • SINGLE-CLICK ô có data → chọn (highlight viền vàng)\n"
                "   • Ctrl+C → sao chép, Ctrl+V → dán vào ô khác\n"
                "   • DRAG (kéo thả) ô có data sang ô đích → tự động copy\n"
                "   • Delete → xóa tiết đang chọn\n"
                "   • Right-click → menu Sao chép / Dán / Xóa\n\n"
                "Công cụ tự lấy danh sách chuẩn từ VnEdu — bạn không cần gõ tay.\n\n"
                "Nếu lịch tuần lẻ và tuần chẵn của bạn KHÁC nhau, "
                "tick vào ô \"Tôi dạy khác nhau giữa tuần lẻ và tuần chẵn\". "
                "Lúc này sẽ có 2 tab: TKB lẻ và TKB chẵn — bạn chỉ cần sửa "
                "vài tiết khác biệt giữa 2 tuần."
            ),
        },
        {
            "title": "Bước 3 — Đặt PPCT bắt đầu",
            "image": "buoc3_ppct.png",
            "body": (
                "Sau khi soạn xong, bảng \"Đặt PPCT bắt đầu\" tự động liệt kê "
                "các nhóm (Lớp × Phân môn) mà bạn dạy.\n\n"
                "Bạn nhập số PPCT bắt đầu cho TUẦN ĐẦU TIÊN của dải tuần "
                "bạn sắp nhập.\n\n"
                "Ví dụ: nếu bạn nhập từ tuần 1 và lớp 6A4 môn Ngoại ngữ "
                "phân môn TC Ngoại ngữ là PPCT 1, thì điền số 1.\n"
                "Công cụ sẽ tự tính PPCT cho các tuần kế tiếp."
            ),
        },
        {
            "title": "Bước 4 — Chọn tuần và Bắt đầu nhập",
            "image": "buoc4_apply.png",
            "body": (
                "Nhập \"Từ tuần\" và \"Đến tuần\" bạn muốn áp dụng "
                "(ví dụ 1 đến 22).\n\n"
                "Bấm \"Xem trước\" để chạy thử (không lưu thật) — "
                "kiểm tra xem PPCT và tên bài có đúng không.\n\n"
                "Bấm \"Bắt đầu nhập\" để áp dụng thật vào VnEdu. "
                "Công cụ sẽ tự lặp tuần lẻ/chẵn theo chữ số cuối của số tuần "
                "(0,2,4,6,8 = chẵn; 1,3,5,7,9 = lẻ).\n\n"
                "Nếu cần dừng, bấm nút \"Dừng\" hoặc phím Esc."
            ),
        },
    ]

    # Thư mục cache ảnh hướng dẫn (đặt cạnh file script). Có thể chưa tồn tại.
    HELP_IMAGE_DIR = "khdh_help"

    # Kích thước placeholder ảnh (px).
    IMAGE_W = 560
    IMAGE_H = 200

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Hướng dẫn sử dụng")
        self.configure(background=CLR_PANEL_BG)
        self.geometry("640x560")
        self.resizable(False, False)
        apply_wizard_styles(ttk.Style(self))

        # Giữ reference các PhotoImage để Tk không garbage-collect.
        self._photo_refs: list[tk.PhotoImage] = []

        self._build_ui()

        # Modal nhẹ: transient nhưng không grab_set — giáo viên vẫn nhìn được
        # màn chính. Close X (WM_DELETE_WINDOW) tự destroy → về main.
        self.transient(parent)
        self.bind("<Escape>", lambda e: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        # Đặt focus vào Notebook để hoạt động bằng phím mũi tên ↔ chuyển tab.
        try:
            self.notebook.focus_set()
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # UI build
    # ------------------------------------------------------------------
    def _build_ui(self):
        body = ttk.Frame(self, padding=16, style="Wiz.TFrame")
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        # Notebook 4 tab — mỗi tab 1 bước.
        self.notebook = ttk.Notebook(body)
        self.notebook.grid(row=0, column=0, sticky="nsew")

        for idx, page in enumerate(self.PAGES, start=1):
            tab = ttk.Frame(self.notebook, padding=12, style="Wiz.TFrame")
            tab.columnconfigure(0, weight=1)
            tab.rowconfigure(2, weight=1)

            # Title
            ttk.Label(
                tab, text=page["title"], style="WizTitle.TLabel"
            ).grid(row=0, column=0, sticky="w", pady=(0, 8))

            # Image placeholder — dùng Label làm canvas duy nhất.
            img_label = self._build_image_placeholder(tab, page.get("image"))
            img_label.grid(row=1, column=0, sticky="ew", pady=(0, 8))

            # Body text — Text widget read-only.
            text_body = tk.Text(
                tab, wrap="word", font=("Segoe UI", 10),
                background="#ffffff", relief="solid", borderwidth=1,
                highlightthickness=0, padx=12, pady=10,
                height=8,
            )
            text_body.insert("end", page["body"])
            text_body.config(state="disabled")
            text_body.grid(row=2, column=0, sticky="nsew")

            self.notebook.add(tab, text=f"  {idx}. {page['title'].split('—')[0].strip()}  ")

        # Toolbar dưới — Quay lại / Tiếp + Đóng + indicator.
        footer = ttk.Frame(body, style="Wiz.TFrame")
        footer.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        footer.columnconfigure(1, weight=1)

        self.btn_prev = ttk.Button(
            footer, text="⬅ Quay lại",
            command=self._on_prev,
            style="WizSubtle.TButton",
        )
        self.btn_prev.grid(row=0, column=0)

        self.var_indicator = tk.StringVar()
        ttk.Label(
            footer, textvariable=self.var_indicator,
            style="WizSubtitle.TLabel",
        ).grid(row=0, column=1)

        self.btn_next = ttk.Button(
            footer, text="Tiếp ➡",
            command=self._on_next,
            style="WizPrimary.TButton",
        )
        self.btn_next.grid(row=0, column=2)

        ttk.Button(
            footer, text="Đóng", command=self.destroy,
            style="WizSubtle.TButton",
        ).grid(row=0, column=3, padx=(8, 0))

        # Khi giáo viên click trực tiếp tab header, cập nhật toolbar state.
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        self._refresh_toolbar()

    def _build_image_placeholder(self, parent: ttk.Frame, image_name: str | None):
        """Trả về widget hiển thị ảnh.

        Ưu tiên load PNG từ ``khdh_help/<image_name>``. Nếu không có hoặc
        load fail → fallback ``Label`` text in giữa khung viền nhạt
        (Requirement: "fallback Label text khi chưa có ảnh").
        """
        photo = self._try_load_image(image_name)
        if photo is not None:
            self._photo_refs.append(photo)
            lbl = ttk.Label(
                parent, image=photo,
                anchor="center", style="Wiz.TLabel",
            )
            return lbl

        # Fallback — placeholder text, kích thước cố định để layout ổn định.
        placeholder = tk.Label(
            parent,
            text="(Ảnh minh họa sẽ thêm sau)",
            font=("Segoe UI", 9, "italic"),
            foreground=CLR_HINT,
            background="#ffffff",
            relief="solid",
            borderwidth=1,
            width=self.IMAGE_W // 8,    # rough char width → ~70 chars
            height=self.IMAGE_H // 18,  # rough line height → ~11 lines
        )
        return placeholder

    def _try_load_image(self, image_name: str | None) -> tk.PhotoImage | None:
        """Thử load PNG từ ``khdh_help/<image_name>``. Trả None nếu fail.

        Tk chỉ hỗ trợ GIF/PGM/PPM/PNG mặc định — đủ cho placeholder ảnh
        dạng PNG. Nếu cần JPG → cần Pillow (bỏ qua trong phase này).
        """
        if not image_name:
            return None
        try:
            script_dir = str(TOOL_DIR)
        except NameError:
            script_dir = os.getcwd()
        path = os.path.join(script_dir, self.HELP_IMAGE_DIR, image_name)
        if not os.path.isfile(path):
            return None
        try:
            return tk.PhotoImage(file=path)
        except tk.TclError:
            # File tồn tại nhưng Tk không decode được — fallback text.
            return None

    # ------------------------------------------------------------------
    # Toolbar actions
    # ------------------------------------------------------------------
    def _current_index(self) -> int:
        try:
            return self.notebook.index(self.notebook.select())
        except tk.TclError:
            return 0

    def _on_tab_changed(self, _event=None):
        self._refresh_toolbar()

    def _refresh_toolbar(self):
        idx = self._current_index()
        total = len(self.PAGES)
        self.var_indicator.set(f"Trang {idx + 1} / {total}")
        self.btn_prev.config(state="normal" if idx > 0 else "disabled")
        self.btn_next.config(state="normal" if idx < total - 1 else "disabled")

    def _on_prev(self):
        idx = self._current_index()
        if idx > 0:
            self.notebook.select(idx - 1)

    def _on_next(self):
        idx = self._current_index()
        if idx < len(self.PAGES) - 1:
            self.notebook.select(idx + 1)

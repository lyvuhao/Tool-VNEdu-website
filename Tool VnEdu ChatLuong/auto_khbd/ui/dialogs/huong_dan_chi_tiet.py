"""Hộp thoại hướng dẫn sử dụng chi tiết."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..styles import apply_wizard_styles
from ..theme import (
    CLR_BODY,
    CLR_BORDER,
    CLR_ERR,
    CLR_HINT,
    CLR_LINK,
    CLR_NAVY,
    CLR_OK,
    CLR_PANEL_BG,
    CLR_SUBTLE,
    CLR_WARN,
)


# =====================================================================
# Dialog — HuongDanChiTietDialog (HDSD nhiều tình huống)
# =====================================================================

class HuongDanChiTietDialog(tk.Toplevel):
    """Cửa sổ hướng dẫn sử dụng chi tiết, chia theo tình huống thực tế.

    Dùng Text tag để có in đậm, in nghiêng, màu cảnh báo và các khung
    nhấn mạnh. Nội dung ưu tiên ngôn ngữ dễ hiểu cho giáo viên thao tác,
    hạn chế thuật ngữ kỹ thuật.
    """

    PAGES = [
        {
            "tab": "Bắt đầu",
            "title": "HDSD NHANH: LÀM ĐÚNG TỪ ĐẦU",
            "subtitle": "Dành cho lần đầu mở tool hoặc khi cần nhập lại cả dải tuần.",
            "visual": [
                ("1. Đăng nhập", "#2f7adf"),
                ("2. Soạn TKB", "#2fa549"),
                ("3. Kiểm PPCT", "#a86400"),
                ("4. Nhập VnEdu", "#7b4cc2"),
            ],
            "blocks": [
                ("h2", "TOOL NÀY DÙNG ĐỂ LÀM GÌ?"),
                ("text", "Tool giúp soạn lịch dạy một lần, tự lặp theo dải tuần, tự tính PPCT, tự giữ trạng thái Bình thường / Nghỉ / Dạy bù và nhập lên VnEdu."),
                ("important", "QUY TẮC VÀNG: trước khi nhập thật, hãy bấm Xem trước để kiểm tra PPCT và trạng thái. Nếu cần sửa nhiều tuần đã có dữ liệu trên web, hãy sao lưu trước."),
                ("h2", "THỨ TỰ NÊN LÀM"),
                ("step", "1. Đăng nhập VnEdu ở phần trên cùng của tool."),
                ("step", "2. Mở hồ sơ cũ hoặc soạn hồ sơ mới bằng bảng TKB."),
                ("step", "3. Nhập PPCT bắt đầu cho từng lớp / phân môn."),
                ("step", "4. Chọn dải tuần cần áp dụng."),
                ("step", "5. Bấm Xem trước, rà soát, sau đó mới bấm Bắt đầu nhập."),
                ("tip", "MẸO: nếu màn hình nhỏ, ưu tiên bấm Phóng to TKB để soạn lịch trực quan hơn."),
                ("warn", "KHÔNG NÊN vừa sửa trực tiếp trên web vừa chạy tool cùng lúc. Dữ liệu trên VnEdu có thể lưu kiểu cuốn chiếu, dễ tạo trùng PPCT nếu sửa đan xen."),
            ],
        },
        {
            "tab": "Soạn TKB",
            "title": "SOẠN THỜI KHÓA BIỂU TRÊN TOOL",
            "subtitle": "Mục tiêu: tạo đúng ô lớp / môn / phân môn trước khi tính PPCT.",
            "visual": [
                ("Double-click ô tiết", "#2f7adf"),
                ("Chọn lớp", "#e7eef5"),
                ("Chọn môn", "#e7eef5"),
                ("Chọn phân môn", "#e7eef5"),
                ("Lưu ô", "#2fa549"),
            ],
            "blocks": [
                ("h2", "CÁCH NHẬP 1 TIẾT DẠY"),
                ("step", "1. Tại bảng TKB, tìm đúng Thứ / Buổi / Tiết."),
                ("step", "2. Double-click vào ô đó."),
                ("step", "3. Chọn Lớp, Môn học, Phân môn theo danh sách tool lấy từ VnEdu."),
                ("step", "4. Bấm Lưu. Ô đã có tiết sẽ đổi màu để dễ nhìn."),
                ("h2", "THAO TÁC NHANH"),
                ("bullet", "Click 1 lần vào ô đã có dữ liệu để chọn ô."),
                ("bullet", "Ctrl+C để sao chép, Ctrl+V để dán sang ô khác."),
                ("bullet", "Kéo thả ô đã có dữ liệu sang ô khác để sao chép nhanh."),
                ("bullet", "Phím Delete để xóa ô đang chọn."),
                ("bullet", "Chuột phải vào ô để mở menu thao tác nhanh."),
                ("h2", "TUẦN LẺ / TUẦN CHẴN"),
                ("text", "Nếu lịch tuần lẻ và tuần chẵn khác nhau, bật lựa chọn dạy khác nhau giữa tuần lẻ và tuần chẵn. Tool sẽ cho bạn soạn riêng 2 bảng: TKB lẻ và TKB chẵn."),
                ("tip", "Nếu chỉ khác vài tiết, hãy soạn bảng chính trước, sau đó tách lẻ/chẵn và sửa các ô khác biệt. Cách này ít sai hơn."),
                ("warn", "Không để ô có Lớp nhưng thiếu Môn hoặc thiếu Phân môn. Khi nhập lên VnEdu, những ô không đủ dữ liệu thường dễ bị lỗi lưu."),
            ],
        },
        {
            "tab": "PPCT",
            "title": "KIỂM SOÁT PPCT KHÔNG BỊ TRÙNG, KHÔNG BỊ THIẾU",
            "subtitle": "Đây là phần quan trọng nhất vì VnEdu thường không cho lưu khi PPCT bị trùng.",
            "visual": [
                ("PPCT bắt đầu", "#2f7adf"),
                ("Nghỉ: không tăng", "#d9534f"),
                ("Bình thường: tăng", "#2fa549"),
                ("Dạy bù: tăng", "#7b4cc2"),
            ],
            "blocks": [
                ("h2", "CẦN NHẬP GÌ?"),
                ("text", "Bảng PPCT bắt đầu liệt kê từng nhóm Lớp / Môn / Phân môn. Bạn chỉ nhập số PPCT đầu tiên của tuần bắt đầu, tool sẽ tự cộng tiếp theo thứ tự tiết xuất hiện trong TKB."),
                ("important", "VÍ DỤ: nếu tuần 22 bắt đầu Ngoại ngữ 7A4 ở PPCT 64, hãy nhập 64. Tool sẽ tự chạy 64, 65, 66... theo lịch."),
                ("h2", "LOGIC NGHỈ / DẠY BÙ"),
                ("bullet", "Tiết Bình thường: ghi PPCT hiện tại rồi tăng sang số kế tiếp."),
                ("bullet", "Tiết Nghỉ: vẫn ghi PPCT nhưng KHÔNG tăng số. Tuần sau học lại vẫn dùng đúng số đó."),
                ("bullet", "Tiết Dạy bù: ghi PPCT đang cần bù và CÓ tăng số sau khi ghi."),
                ("important", "Mẫu đúng: 64, 65, 66 (Nghỉ), 66, 67, 68 (Dạy bù), 69..."),
                ("warn", "Nếu cùng một lớp / phân môn có 2 tiết active trùng PPCT, VnEdu có thể từ chối lưu. Hãy dùng Rà soát PPCT trước khi ghi thật."),
            ],
        },
        {
            "tab": "Nghỉ / Dạy bù",
            "title": "KHAI BÁO TIẾT NGHỈ VÀ TIẾT DẠY BÙ",
            "subtitle": "Dùng khi nghỉ lễ, nghỉ theo thông báo, hoặc cần bù để đủ số tiết.",
            "visual": [
                ("Chọn ô nguồn", "#2f7adf"),
                ("Chuột phải / Alt", "#e7eef5"),
                ("Chọn Nghỉ hoặc Dạy bù", "#a86400"),
                ("Chọn nhiều tuần/tiết", "#2fa549"),
            ],
            "blocks": [
                ("h2", "CÁCH LÀM NHANH TRÊN BẢNG TKB"),
                ("step", "1. Chọn ô TKB đã có Lớp / Môn / Phân môn."),
                ("step", "2. Chuột phải vào ô, hoặc giữ Alt rồi click ô."),
                ("step", "3. Chọn Đánh dấu tiết Nghỉ hoặc Thêm tiết Dạy bù."),
                ("step", "4. Trong bảng hiện ra, chọn tuần, thứ, buổi, tiết cần áp dụng. Có thể chọn nhiều ô nếu cần."),
                ("step", "5. Lưu lại hồ sơ để lần sau mở tool vẫn còn danh sách Nghỉ / Dạy bù."),
                ("h2", "KHI NÀO CHỌN NGHỈ?"),
                ("text", "Chọn Nghỉ khi tiết đúng lịch nhưng không dạy do nghỉ lễ, nghỉ theo thông báo hoặc trường không học. Tool sẽ giữ PPCT tại chỗ và không tự tăng."),
                ("h2", "KHI NÀO CHỌN DẠY BÙ?"),
                ("text", "Chọn Dạy bù khi cần thêm một tiết ở tuần sau hoặc buổi khác để bù lại phần bị hụt. Tiết Dạy bù được tính PPCT như tiết đang dạy thật."),
                ("warn", "Nếu đặt Dạy bù vào ô đã có tiết thường, tool sẽ hỏi rõ có dùng ô đó cho Dạy bù không. Không nên ghi đè nếu chưa chắc."),
            ],
        },
        {
            "tab": "Nhập VnEdu",
            "title": "NHẬP DỮ LIỆU LÊN VNEDU AN TOÀN",
            "subtitle": "Dùng sau khi TKB, PPCT và Nghỉ / Dạy bù đã được rà soát.",
            "visual": [
                ("Xem trước", "#e7eef5"),
                ("Bắt đầu nhập", "#2fa549"),
                ("Theo dõi tiến trình", "#2f7adf"),
                ("Dừng khi cần", "#d9534f"),
            ],
            "blocks": [
                ("h2", "TRƯỚC KHI BẤM BẮT ĐẦU NHẬP"),
                ("step", "1. Kiểm tra dải tuần: Từ tuần / Đến tuần."),
                ("step", "2. Bấm Xem trước để tool dựng kế hoạch nhưng chưa lưu lên web."),
                ("step", "3. Đọc thông báo PPCT, số tiết Nghỉ, số tiết Dạy bù và cảnh báo nếu có."),
                ("step", "4. Nếu ổn, bấm Bắt đầu nhập."),
                ("h2", "KHI TOOL ĐANG CHẠY"),
                ("bullet", "Không tự bấm lung tung trên tab VnEdu đang được tool điều khiển."),
                ("bullet", "Nếu web tải chậm, tool sẽ tự đợi và kiểm tra đủ ô trước khi lưu."),
                ("bullet", "Nếu thấy sai, bấm Dừng. Sau đó kiểm tra tuần đang dừng trước khi chạy lại."),
                ("important", "Nên bật Hiện tiến trình nổi để biết tool đang xử lý tuần nào, ô nào."),
                ("warn", "Nếu vừa thay đổi TKB hoặc PPCT lớn, nên xóa/khôi phục bằng Sao lưu & sửa lỗi thay vì ghi chồng lên dữ liệu cũ."),
            ],
        },
        {
            "tab": "Tên bài",
            "title": "BẢO VỆ VÀ ĐIỀN TÊN BÀI DẠY",
            "subtitle": "Dùng khi VnEdu chưa tự tải tên bài hoặc quản trị cập nhật tên bài trễ.",
            "visual": [
                ("Bảo vệ tên bài", "#2f7adf"),
                ("Cập nhật fallback", "#a86400"),
                ("Điền tên HĐTN thiếu", "#2fa549"),
            ],
            "blocks": [
                ("h2", "BẢO VỆ TÊN BÀI LÀ GÌ?"),
                ("text", "Khi nhập PPCT mà VnEdu chưa trả về tên bài, tool có thể chèn tạm một dấu cách để tránh mất tiết. Các ô đó được ghi vào log để cập nhật lại sau."),
                ("h2", "NÚT CẬP NHẬT TÊN BÀI ĐÃ FALLBACK"),
                ("text", "Dùng khi trước đó tool đã chèn dấu cách, sau này quản trị viên cập nhật phân phối chương trình. Tool sẽ quay lại đúng các ô đã fallback để lấy lại tên bài thật."),
                ("h2", "NÚT ĐIỀN TÊN BÀI HĐTN THIẾU"),
                ("text", "Dùng khi bạn đã nhập đúng KHBD nhưng còn vài ô HĐTN lớp 6/8 bị trống tên bài. Tool đọc dữ liệu tên bài từ Word đã tích hợp rồi chỉ điền các ô còn trống trong dải tuần đang chọn."),
                ("important", "Nút này không đổi lớp, môn, phân môn, PPCT hay trạng thái. Nó chỉ điền Tên bài dạy còn trống."),
                ("tip", "Sau khi chạy xong, hộp thông báo sẽ liệt kê tuần và ô nào đã điền, ô nào chưa tìm thấy tên trong Word để bạn kiểm tra."),
            ],
        },
        {
            "tab": "Sao lưu",
            "title": "SAO LƯU, KHÔI PHỤC VÀ SỬA LỖI",
            "subtitle": "Dùng khi cần sửa dữ liệu đã có trên web hoặc tạo file JSON/Excel để rà soát.",
            "visual": [
                ("Sao lưu", "#2f7adf"),
                ("Rà soát PPCT", "#a86400"),
                ("Khôi phục", "#2fa549"),
                ("Snapshot", "#7b4cc2"),
            ],
            "blocks": [
                ("h2", "KHI NÀO PHẢI SAO LƯU?"),
                ("bullet", "Trước khi sửa hàng loạt nhiều tuần."),
                ("bullet", "Trước khi khôi phục dữ liệu từ file backup."),
                ("bullet", "Trước khi thay đổi logic Nghỉ / Dạy bù hoặc PPCT."),
                ("h2", "KHÔI PHỤC DỮ LIỆU"),
                ("text", "Trong tab Khôi phục, chọn tệp sao lưu, chọn các tuần cần lấy lại, rồi bấm Khôi phục các tuần đã chọn."),
                ("important", "LOGIC MỚI: dù chọn cách nào, tool luôn xóa các tuần đã chọn trên VnEdu trước rồi mới ghi lại từ backup. Cách này tránh trùng dữ liệu cuốn chiếu."),
                ("warn", "Nếu bật Tự tạo bản lưu dự phòng mà snapshot lỗi, tool sẽ dừng trước khi xóa. Đây là chặn an toàn để không làm mất dữ liệu web."),
                ("h2", "RÀ SOÁT PPCT"),
                ("text", "Tab Rà soát PPCT giúp phát hiện trùng PPCT, thiếu PPCT, sai thứ tự, tiết Nghỉ chưa được dạy lại hoặc vượt số tiết tối đa."),
            ],
        },
        {
            "tab": "Lỗi thường gặp",
            "title": "CÁC TRƯỜNG HỢP DỄ LỖI VÀ CÁCH XỬ LÝ",
            "subtitle": "Mở tab này khi tool báo lỗi hoặc web không lưu như mong muốn.",
            "visual": [
                ("Không kết nối", "#d9534f"),
                ("Web tải chậm", "#a86400"),
                ("Trùng PPCT", "#d9534f"),
                ("Thiếu tên bài", "#2f7adf"),
            ],
            "blocks": [
                ("h2", "KHÔNG KẾT NỐI ĐƯỢC VNEDU"),
                ("bullet", "Kiểm tra Chrome đã mở bằng tool hoặc đúng cổng debug chưa."),
                ("bullet", "Đăng nhập VnEdu lại nếu web tự thoát phiên."),
                ("bullet", "Đóng bớt tab VnEdu cũ nếu tool chọn nhầm tab."),
                ("h2", "WEB KHÔNG LƯU HOẶC LƯU THIẾU"),
                ("bullet", "Chạy lại Xem trước để xem tuần nào có cảnh báo."),
                ("bullet", "Nếu sửa nhiều tuần đã có dữ liệu, dùng Khôi phục để xóa tuần trước rồi ghi lại."),
                ("bullet", "Không thao tác tay trên web trong lúc tool đang nhập."),
                ("h2", "TRÙNG PPCT"),
                ("text", "Mở Sao lưu & sửa lỗi > Rà soát PPCT. Nếu có trùng active, cần sửa trước khi ghi lên VnEdu vì web thường không cho lưu."),
                ("h2", "THIẾU TÊN BÀI"),
                ("text", "Nếu là HĐTN lớp 6/8, dùng nút Điền tên bài HĐTN thiếu. Nếu là tên bài đã fallback bằng dấu cách, dùng Cập nhật Tên bài đã fallback."),
                ("important", "KHI KHÔNG CHẮC: sao lưu tuần hiện tại trước, rồi mới sửa. File sao lưu là đường lui an toàn nhất."),
            ],
        },
    ]

    def __init__(self, parent):
        super().__init__(parent)
        self.title("HDSD chi tiết - Tool nhập KHBD tự động")
        self.configure(background=CLR_PANEL_BG)
        self.geometry("980x690")
        self.minsize(860, 600)
        apply_wizard_styles(ttk.Style(self))

        self._text_widgets: list[tk.Text] = []
        self._build_ui()
        self._center_on_parent(parent)

        self.transient(parent)
        self.bind("<Escape>", lambda _event: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        try:
            self.notebook.focus_set()
        except tk.TclError:
            pass

    def _center_on_parent(self, parent):
        """Căn cửa sổ HDSD vào giữa màn hình, tránh bị khuất taskbar."""
        try:
            self.update_idletasks()
            screen_w = self.winfo_screenwidth()
            screen_h = self.winfo_screenheight()
            w = min(max(self.winfo_width(), 980), max(720, screen_w - 80))
            h = min(max(self.winfo_height(), 690), max(560, screen_h - 100))
            x = max(20, (screen_w - w) // 2)
            y = max(20, (screen_h - h) // 2)
            self.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            try:
                self.geometry("980x690+80+40")
            except Exception:
                pass

    def _build_ui(self):
        root = ttk.Frame(self, padding=14, style="Wiz.TFrame")
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        header = ttk.Frame(root, style="Wiz.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        header.columnconfigure(0, weight=1)
        ttk.Label(
            header,
            text="📖 HƯỚNG DẪN SỬ DỤNG CHI TIẾT",
            style="WizTitle.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text=(
                "Chọn tab theo đúng tình huống bạn đang gặp. Các dòng màu đỏ là "
                "cảnh báo cần đọc kỹ trước khi ghi dữ liệu lên VnEdu."
            ),
            style="WizSubtitle.TLabel",
            wraplength=780,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))

        self.notebook = ttk.Notebook(root)
        self.notebook.grid(row=1, column=0, sticky="nsew")

        for page in self.PAGES:
            tab = ttk.Frame(self.notebook, padding=12, style="Wiz.TFrame")
            tab.columnconfigure(0, weight=1)
            tab.rowconfigure(2, weight=0)

            ttk.Label(
                tab, text=page["title"], style="WizTitle.TLabel",
            ).grid(row=0, column=0, sticky="w", pady=(0, 2))
            ttk.Label(
                tab, text=page["subtitle"], style="WizSubtitle.TLabel",
                wraplength=900, justify="left",
            ).grid(row=1, column=0, sticky="w", pady=(0, 8))

            canvas = tk.Canvas(
                tab, height=92, background="#ffffff",
                highlightthickness=1, highlightbackground=CLR_BORDER,
            )
            canvas.grid(row=2, column=0, sticky="ew", pady=(0, 10))
            canvas.bind(
                "<Configure>",
                lambda event, c=canvas, p=page: self._draw_visual(c, p),
            )

            text_wrap = ttk.Frame(tab, style="Wiz.TFrame")
            text_wrap.grid(row=3, column=0, sticky="nsew")
            tab.rowconfigure(3, weight=1)
            text_wrap.columnconfigure(0, weight=1)
            text_wrap.rowconfigure(0, weight=1)

            text = tk.Text(
                text_wrap,
                wrap="word",
                background="#ffffff",
                foreground=CLR_BODY,
                padx=16,
                pady=14,
                relief="solid",
                borderwidth=1,
                highlightthickness=0,
                font=("Segoe UI", 11),
                spacing1=3,
                spacing3=5,
            )
            sb = ttk.Scrollbar(text_wrap, orient="vertical", command=text.yview)
            text.configure(yscrollcommand=sb.set)
            text.grid(row=0, column=0, sticky="nsew")
            sb.grid(row=0, column=1, sticky="ns")
            self._configure_text_tags(text)
            self._insert_page_text(text, page)
            text.configure(state="disabled")
            self._text_widgets.append(text)

            self.notebook.add(tab, text=f"  {page['tab']}  ")

        footer = ttk.Frame(root, style="Wiz.TFrame")
        footer.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        footer.columnconfigure(1, weight=1)

        self.btn_help_prev = ttk.Button(
            footer, text="⬅ Trang trước",
            command=self._on_prev, style="WizSubtle.TButton",
        )
        self.btn_help_prev.grid(row=0, column=0, sticky="w")
        self.var_page_indicator = tk.StringVar(value="")
        ttk.Label(
            footer, textvariable=self.var_page_indicator,
            style="WizSubtitle.TLabel",
        ).grid(row=0, column=1)
        self.btn_help_next = ttk.Button(
            footer, text="Trang sau ➡",
            command=self._on_next, style="WizPrimary.TButton",
        )
        self.btn_help_next.grid(row=0, column=2, padx=(8, 0))
        ttk.Button(
            footer, text="Đóng", command=self.destroy,
            style="WizSubtle.TButton",
        ).grid(row=0, column=3, padx=(8, 0))

        self.notebook.bind("<<NotebookTabChanged>>", lambda _e: self._refresh_footer())
        self._refresh_footer()

    def _configure_text_tags(self, text: tk.Text):
        text.tag_configure("h2", font=("Segoe UI", 12, "bold"), foreground=CLR_NAVY, spacing1=8, spacing3=4)
        text.tag_configure("body", font=("Segoe UI", 11), foreground=CLR_BODY, lmargin2=0)
        text.tag_configure("step", font=("Segoe UI", 11, "bold"), foreground=CLR_OK, lmargin1=18, lmargin2=18)
        text.tag_configure("bullet", font=("Segoe UI", 11), foreground=CLR_BODY, lmargin1=22, lmargin2=22)
        text.tag_configure("bullet_dot", font=("Segoe UI", 11, "bold"), foreground=CLR_LINK)
        text.tag_configure("important", font=("Segoe UI", 11, "bold"), foreground="#0c4a6e", background="#e8f4ff", lmargin1=12, lmargin2=12, spacing1=6, spacing3=6)
        text.tag_configure("warn", font=("Segoe UI", 11, "bold"), foreground=CLR_ERR, background="#fff0f0", lmargin1=12, lmargin2=12, spacing1=6, spacing3=6)
        text.tag_configure("tip", font=("Segoe UI", 11, "italic"), foreground=CLR_WARN, background="#fff8e8", lmargin1=12, lmargin2=12, spacing1=6, spacing3=6)

    def _insert_page_text(self, text: tk.Text, page: dict):
        for kind, content in page.get("blocks", []):
            if kind == "h2":
                text.insert("end", f"{content}\n", "h2")
            elif kind == "step":
                text.insert("end", f"{content}\n", "step")
            elif kind == "bullet":
                text.insert("end", "• ", "bullet_dot")
                text.insert("end", f"{content}\n", "bullet")
            elif kind == "important":
                text.insert("end", f"  {content}\n", "important")
            elif kind == "warn":
                text.insert("end", f"  {content}\n", "warn")
            elif kind == "tip":
                text.insert("end", f"  {content}\n", "tip")
            else:
                text.insert("end", f"{content}\n", "body")
            text.insert("end", "\n", "body")

    def _draw_visual(self, canvas: tk.Canvas, page: dict):
        canvas.delete("all")
        width = max(canvas.winfo_width(), 780)
        labels = page.get("visual") or []
        if not labels:
            return
        canvas.create_text(
            16, 14, text="Sơ đồ thao tác chính", anchor="w",
            fill=CLR_SUBTLE, font=("Segoe UI", 9, "italic"),
        )
        count = len(labels)
        gap = 12
        start_x = 16
        y1 = 32
        box_h = 42
        available = width - 32 - gap * (count - 1)
        box_w = max(120, available / count)
        for idx, (label, color) in enumerate(labels):
            x1 = start_x + idx * (box_w + gap)
            x2 = x1 + box_w
            fg = "#ffffff" if color not in ("#e7eef5", "#ffffff") else CLR_NAVY
            canvas.create_rectangle(
                x1, y1, x2, y1 + box_h,
                fill=color, outline=CLR_BORDER,
            )
            canvas.create_text(
                (x1 + x2) / 2, y1 + box_h / 2,
                text=label, fill=fg,
                font=("Segoe UI", 10, "bold"),
                width=max(90, int(box_w - 12)),
                justify="center",
            )
            if idx < count - 1:
                ax = x2 + gap / 2
                canvas.create_line(
                    ax - 4, y1 + box_h / 2, ax + 4, y1 + box_h / 2,
                    arrow="last", fill=CLR_HINT,
                )

    def _current_index(self) -> int:
        try:
            return self.notebook.index(self.notebook.select())
        except tk.TclError:
            return 0

    def _refresh_footer(self):
        idx = self._current_index()
        total = len(self.PAGES)
        self.var_page_indicator.set(f"Tab {idx + 1} / {total}: {self.PAGES[idx]['tab']}")
        self.btn_help_prev.configure(state="normal" if idx > 0 else "disabled")
        self.btn_help_next.configure(state="normal" if idx < total - 1 else "disabled")

    def _on_prev(self):
        idx = self._current_index()
        if idx > 0:
            self.notebook.select(idx - 1)

    def _on_next(self):
        idx = self._current_index()
        if idx < len(self.PAGES) - 1:
            self.notebook.select(idx + 1)

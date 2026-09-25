"""Giao diện Tkinter — lớp AutoDaNangApp ghép từ các mixin.

Mục lục module:

    app                   AutoDaNangApp — cửa sổ chính của tool nhập Sổ đầu bài.
    chrome                Mở Chrome debug và kết nối CDP.
    class_stats_dialog    Hộp thoại thống kê lớp và cache thống kê.
    class_stats_render    Hiển thị kết quả thống kê.
    class_stats_run       Chạy thống kê lớp / GV chưa nhập / KHDH chờ.
    class_stats_workers   Worker thống kê chạy nền.
    delete_dialog         Xoá dữ liệu sổ đầu bài theo khoảng tuần.
    inspect               Xem thông tin trang, khôi phục UI VnEdu.
    khdh_rows             Xử lý dữ liệu hàng đỏ KHDH và popup "Chi tiết tiết học" — hàm thuần, không cần trình duyệt.
    layout                Dựng giao diện chính và khu Chrome CDP.
    quick_actions         Chuẩn bị nhanh và tự đăng nhập + chạy.
    schedule_form         Quét form, chọn môn/phân môn và dựng yêu cầu chạy lịch.
    schedule_form_state   Kiểm tra form lịch, preflight KHDH và tóm tắt kết quả.
    schedule_panel        Khu Lịch dạy: dựng panel và chế độ lịch.
    schedule_resume       Lưu/khôi phục tiến trình chạy lịch (resume).
    schedule_run          Chạy / tiếp tục / dừng nhập theo lịch.
    schedule_state        Trạng thái nút và hàng đợi tác vụ UI.
    schedule_worker       Worker nhập theo lịch (thủ công) và kết thúc phiên.
    schedule_worker_khdh  Worker nhập theo lịch ở chế độ KHDH.
    scroll                Cuộn khung và thu gọn / mở rộng cửa sổ.
    settings              Lưu/nạp cấu hình.
    styles                Style ttk và ảnh checkbox.
    teacher_progress      Tiến độ PPCT của giáo viên.
    toolbar_log           Thanh nút và khung log.
    window                Kích thước/vị trí cửa sổ và đóng ứng dụng.
"""

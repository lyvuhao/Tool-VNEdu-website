"""Các thread worker chạy nền (CDP) gửi sự kiện về giao diện qua queue.

Mục lục module:

    backup            Worker sao lưu toàn bộ KHDH ra JSON.
    bootstrap         Worker kết nối Chrome và tải dữ liệu khởi tạo.
    delete_weeks      Worker xoá KHDH theo tuần.
    detect_ppct       Worker dò PPCT bắt đầu.
    executor          Worker chạy PlanExecutor.
    fill_titles       Worker điền tên bài HĐTN còn thiếu.
    login             Worker đăng nhập VnEdu tự động và mở trang KHDH.
    refresh_fallback  Worker cập nhật lại tên bài đã fallback.
    restore           Worker khôi phục KHDH từ file JSON.
    scan              Worker quét KHDH cho tab Nâng cao.
    smart_repair      Worker Smart Repair (sửa PPCT lệch).
    tkb_import        Gom nhóm mẫu TKB quét từ web.
    tkb_scan          Worker quét sức khoẻ và quét TKB toàn năm.
"""

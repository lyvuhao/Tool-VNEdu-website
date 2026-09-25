"""Nghiệp vụ KHDH, không phụ thuộc giao diện.

Mục lục module:

    analyzer/     Phân tích mẫu TKB cả năm, HĐTN và báo cáo sức khoẻ.
    backup/       Sao lưu/khôi phục, rà soát PPCT, snapshot, Smart Repair.
    bootstrap     Tải dữ liệu khởi tạo (lớp, môn, PPCT, TKB) từ trang KHDH.
    catalog       Danh mục bài học (PPCT) và tên bài HĐTN đọc từ file Word.
    client/       KHDHClient — giao tiếp với trang KHDH qua Playwright.
    executor/     PlanExecutor — thực thi kế hoạch điền KHDH, ghép từ các mixin.
    fallback_log  Log các ô đã chèn dấu cách thay tên bài (fallback) — dùng bởi executor và worker.
    parser        Parse response `load` của trang KHDH VnEdu thành dữ liệu tuần/tiết.
    planner       Lập kế hoạch điền tiết theo tuần (WeekPlanner).
    profile/      Hồ sơ KHDH (TKB, PPCT, nghỉ/dạy bù) và đọc/ghi Excel/JSON.
"""

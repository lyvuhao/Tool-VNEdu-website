"""Tool nhập Sổ đầu bài VnEdu qua Chrome CDP (launcher: auto_SĐB.py).

Mục lục module:

    app/    Giao diện Tkinter — lớp AutoDaNangApp ghép từ các mixin.
    cdp/    ChromeBridge — điều khiển trang Sổ đầu bài qua CDP, ghép từ các mixin.
    compat  Thư viện tuỳ chọn: winsound (Windows) và Playwright.
    config  Hằng số ứng dụng: file cấu hình/cache, kích thước cửa sổ, màu giao diện, chế độ lịch.
    main    Điểm khởi chạy của tool Sổ đầu bài.
    paths   Đường dẫn gốc của tool.
"""

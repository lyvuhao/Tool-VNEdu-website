"""Tool Ghi nhận xét (launcher: nhanxet_pro.py).

Mục lục module:

    access       Xử lý danh sách quyền truy cập Sổ điểm (khối/lớp/môn được phép).
    automation/  Điều khiển Chrome (CDP) để đọc/ghi Sổ điểm — lớp VnEduScoreAutomation ghép từ các mixin.
    config       Hằng số cấu hình và đường dẫn file của tool Ghi nhận xét.
    main         Điểm khởi chạy của tool Ghi nhận xét.
    models       Các dataclass mô tả dữ liệu Sổ điểm, rule nhận xét và kết quả ghi.
    paths        Đường dẫn gốc của tool.
    progress     Tiện ích báo tiến độ (progress callback) dùng chung cho automation và GUI.
    rules        Phân tích điều kiện rule nhận xét và chọn nhận xét theo điểm.
    ui/          Giao diện Tkinter — lớp AutoNhanXetV2App ghép từ các mixin.
    write_plan   Lập kế hoạch ghi nhận xét và đánh giá kết quả lưu trên server.
"""

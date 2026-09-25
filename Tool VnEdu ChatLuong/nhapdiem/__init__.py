"""Tool Nhập điểm giọng nói (launcher: nhapdiem_pro.py).

Mục lục module:

    automation/      Automation Sổ điểm cho luồng nhập điểm (quyền, quét, lấy nhanh dữ liệu).
    compat           Các thư viện tuỳ chọn (âm thanh, nhận dạng giọng nói, fuzzy match, HTTP).
    config           Hằng số ứng dụng: tiêu đề, đường dẫn file dữ liệu, màu giao diện, giới hạn.
    excel_import     Nhập điểm từ file Excel/CSV: đọc bảng, đoán cột, khớp học sinh và lập kế hoạch điểm chờ.
    main             Điểm khởi chạy của tool Nhập điểm.
    matching         Hàm đo độ giống chuỗi dùng để khớp tên học sinh.
    models           Enum và dataclass dùng trong tool Nhập điểm.
    paths            Đường dẫn gốc của tool.
    scorebook_core/  Lõi Sổ điểm cho tool Nhập điểm.
    scores           Parse, làm tròn và lập payload điểm.
    selftest         Kiểm tra nhanh logic lõi (`--self-test`).
    storage          Đọc/ghi file JSON an toàn (atomic write, backup file lỗi).
    ui/              Giao diện Tkinter — lớp VnEduStandaloneApp ghép từ các mixin.
    voice/           Nhận dạng giọng nói: thu âm, Google Speech, phiên âm tên, tách tên + điểm.
"""

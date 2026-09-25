"""VNEDU Control Panel — dashboard mở các tool (launcher: vnedu_control_panel2.py).

Mục lục module:

    config             Hằng số: tiêu đề, URL, màu giao diện, tên file cấu hình, mutex.
    custom_tools       Thư mục làm việc của tool, tool tuỳ chỉnh và ghi đè tool mặc định.
    embedded           Giải nén / cập nhật mã nguồn tool nhúng và xác định script cần chạy.
    embedded_payloads  Mã nguồn dự phòng của các tool (gzip + base64).
    events             Event bus nội bộ giữa worker và giao diện.
    log                Logger ghi file cho control panel.
    login              Đăng nhập VNEDU qua Chrome debug.
    main               Điểm khởi chạy control panel (CLI + dashboard).
    paths              Đường dẫn gốc của tool.
    process            Chạy tool trong tiến trình con và khoá chống mở trùng (mutex Windows).
    selftest           Kiểm tra nhanh đóng gói dashboard và file tool (`--self-test`).
    storage            Thư mục dữ liệu ứng dụng và file cấu hình của control panel.
    tool_configs       Đồng bộ file cấu hình dùng chung cho các tool (không lưu mật khẩu).
    tool_registry      Danh sách tool mặc định (script, tiêu đề, mô tả).
    ui/                Giao diện dashboard — lớp ControlPanelApp ghép từ các mixin.
    validation         Kiểm tra URL và cổng debug.
"""

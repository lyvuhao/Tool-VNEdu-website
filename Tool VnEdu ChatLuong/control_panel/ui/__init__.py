"""Giao diện dashboard — lớp ControlPanelApp ghép từ các mixin.

Mục lục module:

    app           ControlPanelApp — cửa sổ dashboard điều phối các tool.
    dashboard     Lọc, nhóm và vẽ thẻ tool trên dashboard.
    health        Kiểm tra sức khoẻ tool và CDP.
    lifecycle     Thông báo toast và đóng ứng dụng.
    maintenance   Mở lại VNEDU, thư mục, sao lưu/khôi phục cấu hình, báo cáo.
    processes     Theo dõi tiến trình tool đang chạy.
    screens       Màn hình đăng nhập, dashboard và luồng đăng nhập.
    style         Style ttk của dashboard.
    tool_cards    Thẻ tool, chạy tool và menu thẻ.
    tool_dialogs  Hộp thoại thêm / thay thế tool.
    tool_manager  Quản lý và xoá tool tuỳ chỉnh.
    widgets       Widget dựng sẵn: panel, nút, chip, hộp thoại thông báo/nhập.
"""

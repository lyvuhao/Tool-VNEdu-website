"""Điều khiển Chrome (CDP) để đọc/ghi Sổ điểm — lớp VnEduScoreAutomation ghép từ các mixin.

Mục lục module:

    access_scan     Quét quyền truy cập Sổ điểm theo khối/lớp/môn.
    browser         Khởi động/kết nối Chrome debug và chọn tab VNEDU.
    client          Lớp VnEduScoreAutomation: điều khiển Chrome (CDP) để đọc/ghi Sổ điểm VNEDU.
    combo           Chọn giá trị trong các combobox ExtJS của Sổ điểm.
    comment_write   Ghi nhận xét vào bảng và xác minh lưu trên server.
    context         Dựng ScorebookContext và danh sách quyền từ snapshot.
    context_select  Chọn khối/học kỳ/lớp/môn trên trang và nạp context.
    login           Đăng nhập VNEDU và mở màn hình Sổ điểm.
    schema          Nhận diện schema cột điểm/nhận xét từ header bảng.
    snapshot        Đọc snapshot cửa sổ Sổ điểm (combo, giá trị ẩn, bảng).
"""

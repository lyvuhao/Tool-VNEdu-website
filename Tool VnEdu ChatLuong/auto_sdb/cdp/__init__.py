"""ChromeBridge — điều khiển trang Sổ đầu bài qua CDP, ghép từ các mixin.

Mục lục module:

    bridge             ChromeBridge — kết nối Chrome đang mở qua CDP và điều khiển trang Sổ đầu bài VnEdu.
    config             Cấu hình kết nối Chrome DevTools Protocol và URL VnEdu.
    connection         Kết nối/ngắt CDP, tìm tab VnEdu, nhận diện trang đăng nhập.
    delete             Tìm và xoá tiết đã nhập.
    dropdowns          Đọc/chọn dropdown Tuần, Lớp và danh sách lớp.
    entry_flow         Luồng nhập hoàn chỉnh: ➕ → điền → lưu → đóng.
    form_fill          Đọc và điền form nhập liệu.
    form_fill_js       JavaScript chạy trong popup "Chi tiết tiết học" (ExtJS 4) của Sổ đầu bài — dùng bởi `fill_form`.
    form_save          Lưu form (qua giao diện hoặc API).
    form_wait          Chờ form sẵn sàng và bấm nút ➕ mở form.
    health             Kiểm tra cổng CDP và liệt kê tab Chrome.
    khdh               Đọc context và các dòng gợi ý theo KHDH.
    lesson_form        Đọc popup tiết học và chờ dữ liệu slot.
    navigation         Điều hướng tới màn Chi tiết sổ đầu bài.
    page_utils         Tiện ích trang: option form, chờ cập nhật, inspect, reload.
    sodaubai_fetch     Lấy dữ liệu sổ đầu bài (từng tuần và hàng loạt) qua service nội bộ của VnEdu.
    sodaubai_fetch_js  JavaScript lấy Sổ đầu bài qua service nội bộ của VnEdu — dùng bởi `fetch_sodaubai_rows(_bulk)`.
    stats              Màn Thống kê nhập sổ đầu bài.
    table              Đọc bảng sổ đầu bài, chế độ gợi ý KHDH, dọn dẹp và chẩn đoán UI.
"""

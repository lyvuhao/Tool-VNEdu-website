"""KHDHWizard — cửa sổ chính, ghép từ các mixin.

Mục lục module:

    connect           Kết nối Chrome và nạp dữ liệu khởi tạo.
    delete_weeks      Xoá KHDH theo tuần.
    guards            Chặn chạy song song các worker CDP; after/destroy an toàn.
    layout            Dựng khung chính, header và các cửa sổ phụ.
    ppct              Sửa PPCT, tự tính HĐTN, health scan, dò PPCT.
    profile           Lưu/mở hồ sơ, resume, file gần đây.
    refresh_fallback  Điền tên bài thiếu và cập nhật tên bài fallback.
    run               Chạy / dừng điền KHDH.
    sections          Dựng các khu: kết nối, TKB, PPCT, tuần, chạy, log.
    slot_clipboard    Copy / paste / xoá tiết.
    slot_grid         Tương tác lưới TKB: click, kéo thả, menu.
    state             Log và làm mới trạng thái giao diện.
    templates         Tách TKB lẻ/chẵn, phóng to TKB, import TKB từ web.
    wizard            KHDHWizard — cửa sổ chính của tool KHDH.
"""

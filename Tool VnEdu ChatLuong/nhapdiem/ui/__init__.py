"""Giao diện Tkinter — lớp VnEduStandaloneApp ghép từ các mixin.

Mục lục module:

    access_cache       Cache quyền truy cập và áp dụng context.
    aliases            Biệt danh (alias) cho học sinh.
    app                Cửa sổ chính VnEduStandaloneApp (Tkinter).
    apply_scores       Ghi điểm chờ lên VNEDU, xoá/làm tròn điểm chờ.
    context            Combobox khối/lớp/môn/cột điểm và đồng bộ context.
    editing            Sửa điểm trực tiếp trên bảng, undo.
    export             Tóm tắt và xuất dữ liệu.
    layout             Dựng giao diện, tooltip và bố cục responsive.
    progress           Log, tiến độ, đồng hồ mic và chạy tác vụ nền.
    ptt                Push-to-talk: phím tắt, thu âm, xử lý audio.
    score_table        Bảng điểm học sinh (Treeview) và quét điểm.
    scorebook          Nạp Sổ điểm và chọn context theo quyền.
    settings           Lưu/nạp cấu hình.
    voice_matching     Khớp tên học sinh từ giọng nói và áp điểm.
    voice_recognition  Nhận dạng giọng nói và chọn transcript tốt nhất.
    voice_state        Gợi ý giọng nói, trạng thái chờ xác nhận, âm báo.
    window             Tiện ích cửa sổ Tkinter: vùng làm việc màn hình, style, focus.
"""

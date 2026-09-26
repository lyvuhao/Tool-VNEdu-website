"""PlanExecutor — thực thi kế hoạch điền KHDH, ghép từ các mixin.

Mục lục module:

    dom_write        Vòng chạy chính và ghi trường PPCT lên DOM.
    errors           Định dạng thông điệp lỗi cho người dùng.
    executor         PlanExecutor: thực thi kế hoạch điền KHDH từng tuần.
    js               JavaScript thao tác DOM form tiết học.
    models           Sự kiện và báo cáo của PlanExecutor.
    resume           Xác minh sau khi lưu và lưu điểm resume.
    week_execution   Thực thi một tuần KHDH.
    week_fill        Điền các ô của một tuần KHDH lên DOM — gọi từ `_execute_week` (week_execution.py).
    week_navigation  Chuyển tuần có xác minh và quét chênh lệch.
"""

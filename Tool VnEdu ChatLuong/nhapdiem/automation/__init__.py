"""Automation Sổ điểm cho luồng nhập điểm (quyền, quét, lấy nhanh dữ liệu).

Mục lục module:

    access_scan  Quét danh sách lớp/môn được phép nhập điểm.
    client       VnEduScoreEntryAutomation: automation Sổ điểm cho luồng nhập điểm.
    fast_fetch   Lấy nhanh dữ liệu điểm qua API VNEDU (bỏ qua combo ExtJS).
    permissions  Kiểm tra quyền nhập điểm và đăng nhập VNEDU (bản dành cho Nhập điểm).
"""

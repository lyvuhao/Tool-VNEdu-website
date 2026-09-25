"""Nhận dạng giọng nói: thu âm, Google Speech, phiên âm tên, tách tên + điểm.

Mục lục module:

    audio          Thu âm push-to-talk và đo mức âm lượng.
    constants      Hằng số và regex cho nhận dạng/parse giọng nói.
    google_speech  Gọi Google Speech API (HTTP keep-alive, mã hoá FLAC).
    parsing        Tách tên và điểm từ câu nói.
    phonetics      Chuẩn hoá phiên âm tên học sinh (tiếng Việt, tiếng Khmer).
"""

"""Các thư viện tuỳ chọn (âm thanh, nhận dạng giọng nói, fuzzy match, HTTP)."""

from __future__ import annotations


try:
    import winsound
except ImportError:  # pragma: no cover - không có trên Linux/macOS
    winsound = None


try:
    import numpy as np
except ImportError:  # pragma: no cover - optional dependency at runtime
    np = None


try:
    import sounddevice as sd
except ImportError:  # pragma: no cover - optional dependency at runtime
    sd = None


try:
    import speech_recognition as sr
except ImportError:  # pragma: no cover - optional dependency at runtime
    sr = None


try:
    from rapidfuzz import fuzz as rapidfuzz_fuzz
except ImportError:  # pragma: no cover - optional dependency at runtime
    rapidfuzz_fuzz = None


try:
    from fuzzywuzzy import fuzz as fuzzywuzzy_fuzz
except ImportError:  # pragma: no cover - optional dependency at runtime
    fuzzywuzzy_fuzz = None


try:  # PERF: HTTP keep-alive cho Google Speech API (cắt 200–500ms/request)
    import requests as _perf_requests
    from requests.adapters import HTTPAdapter as _PerfHTTPAdapter
except ImportError:  # pragma: no cover - optional dependency at runtime
    _perf_requests = None
    _PerfHTTPAdapter = None


try:  # PERF #3: in-process FLAC encoder (~70–120ms/clip nhanh hơn flac.exe của sr)
    import soundfile as _perf_soundfile
except ImportError:  # pragma: no cover - optional dependency at runtime
    _perf_soundfile = None

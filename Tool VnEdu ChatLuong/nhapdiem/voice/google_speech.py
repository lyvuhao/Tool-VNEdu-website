"""Gọi Google Speech API (HTTP keep-alive, mã hoá FLAC)."""

from __future__ import annotations

import json
import threading
from typing import Any

from ..compat import _perf_requests, _perf_soundfile, _PerfHTTPAdapter, np, sr
from .constants import (
    VOICE_HTTP_CONNECT_TIMEOUT,
    VOICE_HTTP_POOL_SIZE,
    VOICE_HTTP_PREWARM_TIMEOUT,
    VOICE_HTTP_READ_TIMEOUT,
)


# Google Speech v2 endpoint + key Chromium công khai (giống speech_recognition dùng).
_VOICE_GOOGLE_ENDPOINT = "http://www.google.com/speech-api/v2/recognize"


_VOICE_GOOGLE_DEFAULT_KEY = "AIzaSyBOti4mM-6x9WDnZIjIeyEU21OpBXqWBgw"


_VOICE_HTTP_SESSION_LOCK = threading.Lock()


_VOICE_HTTP_SESSION: Any | None = None


_VOICE_HTTP_PREWARM_DONE = False


def _voice_http_session() -> Any | None:
    """Returns one shared HTTPS session with keep-alive enabled.

    PERF #2: tạo Session 1 lần và tái sử dụng → tiết kiệm 200–500ms TLS mỗi request.
    Trả về None nếu chưa cài `requests`; khi đó pipeline sẽ fallback về urllib (sr gốc).
    """
    global _VOICE_HTTP_SESSION, _VOICE_HTTP_PREWARM_DONE
    if _perf_requests is None or _PerfHTTPAdapter is None:
        return None
    with _VOICE_HTTP_SESSION_LOCK:
        if _VOICE_HTTP_SESSION is None:
            session = _perf_requests.Session()
            adapter = _PerfHTTPAdapter(
                pool_connections=VOICE_HTTP_POOL_SIZE,
                pool_maxsize=VOICE_HTTP_POOL_SIZE,
                max_retries=0,
            )
            session.mount("http://", adapter)
            session.mount("https://", adapter)
            _VOICE_HTTP_SESSION = session
        if not _VOICE_HTTP_PREWARM_DONE:
            _VOICE_HTTP_PREWARM_DONE = True

            def _warm_up() -> None:
                try:
                    _VOICE_HTTP_SESSION.head(  # type: ignore[union-attr]
                        _VOICE_GOOGLE_ENDPOINT,
                        timeout=VOICE_HTTP_PREWARM_TIMEOUT,
                        allow_redirects=False,
                    )
                except Exception:
                    pass

            threading.Thread(
                target=_warm_up,
                name="VoiceHTTPPrewarm",
                daemon=True,
            ).start()
        return _VOICE_HTTP_SESSION


def _voice_encode_flac_fast(audio_int16: Any, sample_rate: int) -> bytes | None:
    """Encodes int16 audio to FLAC in-process via libsndfile.

    PERF #3: Bỏ qua `flac.exe` của speech_recognition (chậm 70–120ms cho clip 1s
    do spawn subprocess). libsndfile in-process chỉ tốn ~5–15ms.

    Returns FLAC bytes, hoặc None nếu `soundfile` chưa cài / audio không hợp lệ
    (caller fallback về `sr.AudioData.get_flac_data()` cũ).
    """
    if _perf_soundfile is None or np is None or audio_int16 is None:
        return None
    try:
        if hasattr(audio_int16, "size") and audio_int16.size <= 0:
            return None
        # libsndfile yêu cầu PCM. int16 là tối ưu cho 16kHz speech (8kbps thực).
        if audio_int16.dtype != np.int16:
            audio_int16 = audio_int16.astype(np.int16)
        import io as _io  # nội bộ — chỉ dùng khi encode FLAC

        buffer = _io.BytesIO()
        _perf_soundfile.write(buffer, audio_int16, int(sample_rate), format="FLAC")
        return buffer.getvalue()
    except Exception:  # pragma: no cover - guard libsndfile errors
        return None


def _voice_recognize_google_session(
    recognizer: Any,
    audio_obj: Any,
    *,
    language: str = "vi-VN",
    show_all: bool = True,
    phrase_hints: list[str] | None = None,
    api_key: str | None = None,
    connect_timeout: float = VOICE_HTTP_CONNECT_TIMEOUT,
    read_timeout: float = VOICE_HTTP_READ_TIMEOUT,
    flac_bytes: bytes | None = None,
    sample_rate_override: int | None = None,
) -> Any:
    """Calls Google Speech v2 over a keep-alive session and returns the parsed payload.

    Compatible với `sr.Recognizer.recognize_google` về mặt return: trả dict khi
    show_all=True, raise `sr.UnknownValueError` khi không nghe được, raise
    `sr.RequestError` khi mạng/HTTP lỗi.

    Args:
        flac_bytes: Tuỳ chọn — FLAC bytes đã encode sẵn (PERF #3) để bỏ qua
            `audio_obj.get_flac_data()` (gọi flac.exe chậm). Khi truyền vào,
            `sample_rate_override` cũng phải set.
    """
    if sr is None:
        raise RuntimeError("Thiếu speech_recognition để gọi Google Speech.")
    # Nếu caller truyền hint VÀ recognizer gốc hỗ trợ `phrase_list`, dùng path gốc
    # (mất keep-alive nhưng giữ tính năng hint thật). Endpoint v2 công khai chưa hỗ
    # trợ hint qua query, nên session bỏ qua hint là tương đương no-op.
    if phrase_hints:
        try:
            kwargs: dict[str, Any] = {"language": language, "show_all": show_all}
            kwargs["phrase_list"] = list(phrase_hints)
            return recognizer.recognize_google(audio_obj, **kwargs)  # type: ignore[no-any-return]
        except TypeError:
            # sr không hỗ trợ phrase_list → bỏ hint, đi tiếp vào session keep-alive bên dưới.
            pass
    session = _voice_http_session()
    if session is None:
        # Không có requests — fallback hoàn toàn về sr gốc (urllib mỗi lần mở TLS mới).
        return recognizer.recognize_google(  # type: ignore[no-any-return]
            audio_obj,
            language=language,
            show_all=show_all,
        )

    # PERF #3: ưu tiên FLAC đã pre-encode bằng libsndfile.
    if flac_bytes is not None and sample_rate_override:
        flac_data = flac_bytes
        effective_sample_rate = int(sample_rate_override)
    else:
        try:
            flac_data = audio_obj.get_flac_data(
                convert_rate=None if audio_obj.sample_rate >= 8000 else 8000,
                convert_width=2,
            )
            effective_sample_rate = int(audio_obj.sample_rate)
        except AttributeError:
            # audio_obj không phải sr.AudioData chuẩn (ví dụ: mock trong self-test).
            # Fallback về recognizer gốc — không có keep-alive nhưng vẫn chạy đúng.
            # Chỉ truyền các kwargs cần thiết để khớp với mock recognizer trong test.
            kwargs: dict[str, Any] = {"language": language}
            if show_all:
                kwargs["show_all"] = True
            if phrase_hints:
                kwargs["phrase_list"] = phrase_hints
            return recognizer.recognize_google(audio_obj, **kwargs)  # type: ignore[no-any-return]
        except Exception as error:  # pragma: no cover - sr internal
            raise sr.RequestError(f"Không encode được audio: {error}") from error

    headers = {
        "Content-Type": f"audio/x-flac; rate={effective_sample_rate}",
    }
    params = {
        "client": "chromium",
        "lang": language,
        "key": api_key or _VOICE_GOOGLE_DEFAULT_KEY,
    }
    # NOTE: Endpoint Google Speech v2 (key Chromium công khai) KHÔNG hỗ trợ
    # `phrase_list`/`speechContext` qua query param. Smart hints chỉ có tác dụng
    # khi `speech_recognition` nâng cấp tham số `phrase_list` thật (lúc đó
    # `_recognizer_phrase_list_supported = True` và caller sẽ rớt vào nhánh
    # fallback urllib bên dưới). Ta cố ý bỏ trống để tránh gửi param vô dụng.
    _ = phrase_hints  # noqa: F841 - giữ tham số để API tương thích sr.recognize_google

    try:
        response = session.post(
            _VOICE_GOOGLE_ENDPOINT,
            params=params,
            data=flac_data,
            headers=headers,
            timeout=(connect_timeout, read_timeout),
        )
    except _perf_requests.exceptions.RequestException as error:  # type: ignore[union-attr]
        raise sr.RequestError(f"Lỗi kết nối Google: {error}") from error

    if response.status_code != 200:
        raise sr.RequestError(
            f"Google Speech HTTP {response.status_code}",
        )

    body_text = response.text or ""
    actual_payload: dict[str, Any] | None = None
    for raw_line in body_text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict) and parsed.get("result"):
            actual_payload = parsed
            break

    if actual_payload is None:
        if show_all:
            raise sr.UnknownValueError()
        raise sr.UnknownValueError()

    if show_all:
        # Trả result đầu tiên (giống sr gốc).
        first_result = actual_payload["result"][0] if actual_payload.get("result") else {}
        return first_result if isinstance(first_result, dict) else actual_payload

    # show_all=False → trả best transcript dạng str
    alternatives = []
    for entry in actual_payload.get("result", []):
        if isinstance(entry, dict):
            alternatives = entry.get("alternative", []) or []
            if alternatives:
                break
    if not alternatives:
        raise sr.UnknownValueError()
    best = max(
        (alt for alt in alternatives if isinstance(alt, dict) and alt.get("transcript")),
        key=lambda alt: float(alt.get("confidence", 0.0) or 0.0),
        default=None,
    )
    if best is None:
        raise sr.UnknownValueError()
    return str(best.get("transcript", "")).strip()

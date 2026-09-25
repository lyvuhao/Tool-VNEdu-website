"""Push-to-talk: phím tắt, thu âm, xử lý audio."""

from __future__ import annotations

import inspect
import threading
import time
import tkinter as tk
from tkinter import messagebox
from typing import Any, Callable

from ..compat import np, sr
from ..config import APP_SUCCESS, AUDIO_SAMPLE_RATE, MIC_CONSECUTIVE_ERROR_MAX
from ..models import LogTag
from ..voice.audio import PTTCaptureStream
from ..voice.constants import (
    VOICE_NORMALIZE_TARGET_PEAK,
    VOICE_TRIM_LEAD_MARGIN_MS,
    VOICE_TRIM_MIN_DURATION,
    VOICE_TRIM_TAIL_MARGIN_MS,
)
from ..voice.google_speech import _voice_encode_flac_fast, _voice_http_session
from .window import _is_text_input_focus


class PushToTalkMixin:
    """Push-to-talk: phím tắt, thu âm, xử lý audio."""

    def _bind_ptt_root_event(self, key: str, sequence: str, callback: Callable[[tk.Event], object]) -> None:
        """Binds one PTT-owned root event once and remembers its Tk funcid."""
        if key in self._ptt_bind_ids:
            return
        try:
            funcid = self.root.bind(sequence, callback, add="+")
        except tk.TclError:
            return
        if funcid:
            self._ptt_bind_ids[key] = (sequence, funcid)

    def _unbind_ptt_root_event(self, key: str) -> None:
        """Unbinds only the PTT-owned callback for one root event."""
        binding = self._ptt_bind_ids.pop(key, None)
        if binding is None:
            return
        sequence, funcid = binding
        try:
            self.root.unbind(sequence, funcid)
        except tk.TclError:
            pass

    def _bind_ptt_space_bindings(self) -> None:
        """Enables Space press/release handlers owned by PTT."""
        self._bind_ptt_root_event("space_press", "<KeyPress-space>", self._on_space_press)
        self._bind_ptt_root_event("space_release", "<KeyRelease-space>", self._on_space_release)

    def _unbind_ptt_space_bindings(self) -> None:
        """Disables only PTT Space handlers without touching other shortcuts."""
        self._unbind_ptt_root_event("space_press")
        self._unbind_ptt_root_event("space_release")

    def _bind_ptt_keyboard(self) -> None:
        """Enables all keyboard handlers owned by the PTT mode."""
        self._bind_ptt_space_bindings()
        self._bind_ptt_root_event("focus_out", "<FocusOut>", self._on_root_focus_out)

    def _unbind_ptt_keyboard(self) -> None:
        """Disables all keyboard handlers owned by the PTT mode."""
        self._unbind_ptt_space_bindings()
        self._unbind_ptt_root_event("focus_out")

    def toggle_ptt(self) -> None:
        if self._ptt_enabled:
            self._disable_ptt()
            return
        try:
            self._require_voice_ready()
            self._init_recognizer()
            self._ensure_ptt_capture_stream()
            self._ensure_ptt_worker()
            # PERF #2: bật session HTTP keep-alive + prewarm TLS handshake ngay khi
            # user bật bộ đàm (không phải đợi đến request đầu tiên).
            _voice_http_session()
            self._ensure_voice_recognize_executor()
        except Exception as error:  # noqa: BLE001
            messagebox.showerror("Không bật được bộ đàm", str(error))
            self._log(f"Lỗi bộ đàm: {error}")
            return
        self._build_voice_hints()
        self._ptt_enabled = True
        self._space_pressed = False
        self.btn_ptt_toggle.config(text="🎤 BỘ ĐÀM: BẬT", bg=APP_SUCCESS, activebackground="#15803d")
        self.btn_ptt_toggle._hover_prev_bg = APP_SUCCESS  # type: ignore[attr-defined]  # đồng bộ hover
        self.voice_status_var.set("⏸️ Sẵn sàng. Giữ Space để nói, thả ra để xử lý.")
        self._bind_ptt_keyboard()
        self._render_voice_meter()
        self._focus_preview_tree()
        self._log("Bộ đàm đã bật.")

    def _disable_ptt(self) -> None:
        self._ptt_enabled = False
        self._space_pressed = False
        self._ptt_recording = False
        self._ptt_audio_buffer = None
        self._clear_voice_pending()
        self._clear_voice_tied_candidates()  # KHMER #B
        self._unbind_ptt_keyboard()
        if self._ptt_capture is not None:
            self._ptt_capture.stop()
            self._ptt_capture = None
        if hasattr(self, "btn_ptt_toggle"):
            try:
                self.btn_ptt_toggle.config(text="🎤 BỘ ĐÀM: TẮT", bg="#64748b", activebackground="#475569")
                self.btn_ptt_toggle._hover_prev_bg = "#64748b"  # type: ignore[attr-defined]  # đồng bộ hover
            except tk.TclError:
                pass
        if hasattr(self, "voice_status_var"):
            try:
                self.voice_status_var.set("⏸️ Bộ đàm đang tắt")
            except tk.TclError:
                pass
        self._reset_voice_meter()

    def _on_root_focus_out(self, _event: tk.Event | None = None) -> None:
        """BUG-02 FIX: Reset Space key state khi cửa sổ mất focus.

        Khi user Alt+Tab hoặc click ra ngoài trong lúc giữ Space,
        KeyRelease-space không fire → _space_pressed kẹt True vĩnh viễn.
        Handler này đảm bảo trạng thái được reset.
        """
        if self._space_pressed:
            self._space_pressed = False
            self._stop_ptt_recording()

    def _on_escape_cancel_recording(self, _event: tk.Event | None = None) -> str:
        """IMP-B2: Hủy recording khi nhấn Escape trong lúc đang giữ Space.

        Nếu đang recording (Space pressed), hủy ngay và không xử lý audio.
        Nếu không đang recording, không làm gì (để Escape hoạt động bình thường).
        """
        if self._space_pressed and self._ptt_recording:
            self._space_pressed = False
            # Hủy recording mà không xử lý audio
            with self._ptt_lock:
                if self._ptt_recording:
                    self._ptt_recording = False
            try:
                if self._ptt_capture is not None:
                    self._ptt_capture.end_recording()
            except Exception:
                pass
            self._ptt_audio_buffer = None
            self._render_voice_meter()
            self.voice_status_var.set("⏹️ Đã hủy ghi âm.")
            self._log("Hủy ghi âm bằng Escape.", tag=LogTag.WARNING)
            return "break"
        return ""

    def _show_help_dialog(self, _event: tk.Event | None = None) -> str:
        """IMP-D6: Hiện dialog tổng hợp phím tắt và hướng dẫn sử dụng."""
        help_text = (
            "╔═══════════════════════════════════════════╗\n"
            "║          PHÍM TẮT & HƯỚNG DẪN            ║\n"
            "╠═══════════════════════════════════════════╣\n"
            "║                                           ║\n"
            "║  🎤 Giọng nói (PTT):                     ║\n"
            "║  • Space (giữ)   : Ghi âm giọng nói      ║\n"
            "║  • Space (thả)   : Xử lý nhận dạng       ║\n"
            "║  • Escape         : Hủy ghi âm đang giữ  ║\n"
            "║                                           ║\n"
            "║  📝 Bảng điểm:                            ║\n"
            "║  • Double-click   : Sửa điểm chờ         ║\n"
            "║  • Delete         : Xóa điểm chờ         ║\n"
            "║  • Tab / Shift+Tab: Nhảy row khi sửa     ║\n"
            "║  • Enter          : Xác nhận sửa          ║\n"
            "║  • Ctrl+Z         : Undo lần cuối         ║\n"
            "║  • Ctrl+S         : Ghi điểm lên web      ║\n"
            "║                                           ║\n"
            "║  🔢 Khi trùng điểm (1-5):                 ║\n"
            "║  • 1: Thay thế bằng giá trị mới           ║\n"
            "║  • 2: Giữ nguyên điểm cũ                  ║\n"
            "║  • 3: Lấy trung bình                      ║\n"
            "║  • 4: Cộng thêm 1 điểm                    ║\n"
            "║  • 5: Dồn điểm (tối đa 10)                ║\n"
            "║                                           ║\n"
            "║  ⌨️ Khác:                                  ║\n"
            "║  • F1             : Hiện dialog này        ║\n"
            "╚═══════════════════════════════════════════╝"
        )
        messagebox.showinfo("Hướng dẫn sử dụng", help_text, parent=self.root)
        return "break"

    def _init_recognizer(self) -> None:
        if self._recognizer is not None:
            return
        if sr is None:
            raise RuntimeError("Thiếu speech_recognition.")
        recognizer = sr.Recognizer()
        recognizer.energy_threshold = 300
        recognizer.dynamic_energy_threshold = False
        recognizer.pause_threshold = 0.5
        self._recognizer = recognizer
        try:
            signature = inspect.signature(recognizer.recognize_google)
            self._recognizer_phrase_list_supported = "phrase_list" in signature.parameters
        except (TypeError, ValueError):
            self._recognizer_phrase_list_supported = False

    def _ensure_ptt_capture_stream(self) -> None:
        if self._ptt_capture is None:
            self._ptt_capture = PTTCaptureStream()
        if not self._ptt_capture.start():
            raise RuntimeError("Không thể mở stream micro cho bộ đàm.")

    def _ensure_ptt_worker(self) -> None:
        if self._ptt_worker is not None and self._ptt_worker.is_alive():
            return
        self._ptt_worker_shutdown = False
        self._ptt_worker = threading.Thread(
            target=self._ptt_worker_loop,
            daemon=True,
            name="VnEduPTTWorker",
        )
        self._ptt_worker.start()

    def _stop_ptt_worker(self) -> None:
        self._ptt_worker_shutdown = True
        try:
            self._ptt_queue.put_nowait(None)
        except Exception:
            pass

    def _ptt_worker_loop(self) -> None:
        while not self._ptt_worker_shutdown:
            task = self._ptt_queue.get()
            if task is None:
                continue
            request_id, roster_revision, audio_data, started_at = task
            # BUG-09 FIX: Wrap trong try-except để worker thread không chết khi gặp exception bất ngờ
            try:
                self._process_ptt_audio_task(request_id, roster_revision, audio_data, started_at)
            except Exception as worker_err:
                print(f"[DEBUG] PTT worker error (request {request_id}): {type(worker_err).__name__}: {worker_err}")
                try:
                    self._dispatch_ui_callback(
                        lambda: (
                            self._beep_voice_error(),
                            self.voice_status_var.set("⚠️ Lỗi xử lý giọng nói. Thử lại."),
                        )
                    )
                except Exception:
                    pass

    def _on_space_press(self, _event: tk.Event) -> str | None:
        if not self._ptt_enabled or self._space_pressed or self._busy:
            return "break"
        focused = self.root.focus_get()
        if _is_text_input_focus(focused):
            return None
        self._space_pressed = self._start_ptt_recording()
        return "break"

    def _on_space_release(self, _event: tk.Event) -> str | None:
        if not self._space_pressed:
            return None if _is_text_input_focus(self.root.focus_get()) else "break"
        self._space_pressed = False
        self._stop_ptt_recording()
        return "break"

    def _start_ptt_recording(self) -> bool:
        if self._ptt_capture is None or self._recognizer is None:
            return False
        if not self._ptt_lock.acquire(blocking=False):
            return False
        try:
            if self._ptt_recording:
                return False
            self._ptt_audio_buffer = None
            try:
                started = self._ptt_capture.begin_recording()
            except Exception:
                started = False
            if not started:
                # IMP-C2: Đếm lỗi mic liên tiếp, tự tắt PTT sau MIC_CONSECUTIVE_ERROR_MAX lần
                self._mic_consecutive_errors += 1
                if self._mic_consecutive_errors >= MIC_CONSECUTIVE_ERROR_MAX:
                    self.voice_status_var.set(f"❌ Mic lỗi {self._mic_consecutive_errors} lần liên tiếp — PTT tạm tắt.")
                    self._beep_voice_error()
                    self._render_voice_meter()
                    self._mic_consecutive_errors = 0
                    # Tắt PTT trên main thread (gọi qua after vì đang có thể ở worker)
                    try:
                        self.root.after(0, self._disable_ptt)
                    except Exception:
                        pass
                    return False
                self._beep_voice_error()
                self.voice_status_var.set(f"⚠️ Không thể bắt đầu ghi âm (lần {self._mic_consecutive_errors}/{MIC_CONSECUTIVE_ERROR_MAX}).")
                self._render_voice_meter()
                return False
            self._mic_consecutive_errors = 0  # IMP-C2: Reset đếm lỗi khi ghi âm thành công
            self._ptt_recording = True
            self._beep_recording_start()  # beep báo hiệu bắt đầu thu âm
            pending_key, _pending_at, _pending_revision = self._valid_voice_pending_snapshot()
            with self._score_data_lock:
                pending_row = self._score_rows_by_key.get(pending_key) if pending_key else None
            if pending_row:
                self.voice_status_var.set(f"🔴 Đang nghe điểm cho {pending_row.student_name}...")
            else:
                self.voice_status_var.set("🔴 Đang nghe... thả Space để xử lý")
            self._render_voice_meter()
            return True
        finally:
            self._ptt_lock.release()

    def _stop_ptt_recording(self) -> None:
        # BUG-14 FIX: Acquire lock để đồng bộ với _start_ptt_recording và audio callback
        with self._ptt_lock:
            if not self._ptt_recording:
                return
            self._ptt_recording = False
        self.voice_status_var.set("⏳ Đang xử lý giọng nói...")
        self._render_voice_meter()
        try:
            if self._ptt_capture is not None:
                self._ptt_audio_buffer = self._ptt_capture.end_recording()
        except Exception:
            self._ptt_audio_buffer = None
        if self._ptt_audio_buffer is None:
            self._beep_voice_error()
            self.voice_status_var.set("⚠️ Không ghi được âm thanh.")
            return
        self._ptt_request_seq += 1
        self._ptt_latest_request_id = self._ptt_request_seq
        self._ptt_queue.put((self._ptt_request_seq, self._ptt_roster_revision, self._ptt_audio_buffer, time.perf_counter()))

    def _process_ptt_audio(self) -> None:
        if self._ptt_audio_buffer is None:
            return
        self._ptt_request_seq += 1
        self._ptt_latest_request_id = self._ptt_request_seq
        self._process_ptt_audio_task(self._ptt_request_seq, self._ptt_roster_revision, self._ptt_audio_buffer, time.perf_counter())

    def _trim_ptt_audio(self, audio_float: Any) -> Any:
        if np is None or audio_float is None:
            return audio_float
        if getattr(audio_float, "size", 0) <= 0:
            return audio_float
        max_amp = float(np.max(np.abs(audio_float)))
        if max_amp <= 0.0:
            return audio_float
        trim_threshold = max(0.0035, max_amp * 0.12)
        active_indices = np.flatnonzero(np.abs(audio_float) >= trim_threshold)
        if active_indices.size == 0:
            return audio_float
        lead_margin = int((VOICE_TRIM_LEAD_MARGIN_MS / 1000.0) * AUDIO_SAMPLE_RATE)
        tail_margin = int((VOICE_TRIM_TAIL_MARGIN_MS / 1000.0) * AUDIO_SAMPLE_RATE)
        start_index = max(0, int(active_indices[0]) - lead_margin)
        end_index = min(len(audio_float), int(active_indices[-1]) + tail_margin + 1)
        trimmed = audio_float[start_index:end_index]
        if len(trimmed) / AUDIO_SAMPLE_RATE < VOICE_TRIM_MIN_DURATION:
            return audio_float
        return trimmed

    def _enhance_audio_for_recognition(self, audio: Any, *, boost_db: float = 0.0) -> Any:
        """Chuẩn hóa biên độ tín hiệu trước khi gửi Google Speech API.

        THIẾT KẾ (V1–V3 FIX): Google Speech nhận raw waveform và tự trích đặc
        trưng âm học, nên KHÔNG nên áp pre-emphasis / noise-gate / soft-clip mặc
        định. Các bước đó làm méo phổ, xóa phụ âm yếu (s, x, th, ph, h, âm cuối)
        và bóp dynamic range → giảm độ chính xác với nguyên âm, thanh điệu và
        đuôi từ tiếng Việt (đúng loại lỗi gây nhầm tên).

        - Mặc định (boost_db=0): CHỈ peak-normalize nhẹ để mức âm nhất quán,
          giữ nguyên hình dạng sóng tự nhiên mà model Google kỳ vọng.
        - Khi boost_db>0 (tín hiệu yếu): normalize → +gain → tanh soft-clip để
          tránh vỡ tiếng. tanh chỉ áp khi thực sự có nguy cơ clip do khuếch đại.

        Args:
            audio: numpy float32 array mono.
            boost_db: tăng gain (dB) cho lần thử tín hiệu yếu. Mặc định 0.

        Returns:
            numpy float32 array đã chuẩn hóa.
        """
        if np is None or audio is None or getattr(audio, "size", 0) == 0:
            return audio

        enhanced = audio.astype(np.float32, copy=True)

        # Peak normalization — đưa đỉnh tín hiệu về mức chuẩn, giữ hình dạng sóng.
        peak = float(np.max(np.abs(enhanced)))
        if peak > 1e-5:
            enhanced = enhanced * (VOICE_NORMALIZE_TARGET_PEAK / peak)

        # Chỉ khi cần khuếch đại (tín hiệu yếu): +gain rồi tanh soft-clip chống vỡ tiếng.
        if boost_db > 0.0:
            linear_gain = 10.0 ** (boost_db / 20.0)
            enhanced = np.tanh(enhanced * linear_gain)

        return enhanced.astype(np.float32)

    def _audio_to_speech_obj(self, audio_float: Any) -> Any:
        """
        Chuyển đổi audio float32 → sr.AudioData cho Google Speech API.

        Args:
            audio_float: numpy float32 array (giá trị trong khoảng [-1, 1]).

        Returns:
            sr.AudioData object sẵn sàng cho recognize_google().
        """
        audio_int16 = (audio_float * 32767).astype(np.int16)
        return sr.AudioData(audio_int16.tobytes(), AUDIO_SAMPLE_RATE, sample_width=2)

    def _audio_to_recognition_payload(self, audio_float: Any) -> tuple[Any, bytes | None, int]:
        """Chuyển đổi audio float32 → (sr.AudioData, FLAC bytes, sample_rate).

        PERF #3: pre-encode FLAC bằng libsndfile (~5–15ms) thay vì để
        `audio_obj.get_flac_data()` spawn `flac.exe` (~70–120ms) trong hot path.
        Trả `flac_bytes=None` khi `soundfile` không có hoặc encode lỗi — caller
        rớt về path cũ tự nhiên.
        """
        audio_int16 = (audio_float * 32767).astype(np.int16)
        audio_data = sr.AudioData(audio_int16.tobytes(), AUDIO_SAMPLE_RATE, sample_width=2)
        flac_bytes = _voice_encode_flac_fast(audio_int16, AUDIO_SAMPLE_RATE)
        return audio_data, flac_bytes, AUDIO_SAMPLE_RATE

    def _is_ptt_result_current(self, request_id: int, roster_revision: int) -> bool:
        return (
            self._ptt_enabled
            and not self._busy
            and not self._voice_conflict_dialog_open
            and request_id == self._ptt_latest_request_id
            and roster_revision == self._ptt_roster_revision
        )

    def _dispatch_ptt_result(self, request_id: int, roster_revision: int, callback: Callable[[], None]) -> None:
        def guarded_callback() -> None:
            if not self._is_ptt_result_current(request_id, roster_revision):
                return
            callback()

        self._dispatch_ui_callback(guarded_callback)

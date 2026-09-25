"""Gợi ý giọng nói, trạng thái chờ xác nhận, âm báo."""

from __future__ import annotations

import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from typing import Sequence

from ..compat import np, sd, sr, winsound
from ..config import APP_DANGER, APP_SUCCESS
from ..models import LogTag
from ..voice.constants import (
    VOICE_HINT_EXTENDED_LIMIT,
    VOICE_HINT_PRIMARY_LIMIT,
    VOICE_HINT_SMART_LIMIT,
    VOICE_PENDING_TIMEOUT,
)
from ..voice.phonetics import _normalize_diacritic_text


class VoiceStateMixin:
    """Gợi ý giọng nói, trạng thái chờ xác nhận, âm báo."""

    def _build_voice_hints(self) -> None:
        primary_hints: list[str] = []
        extended_hints: list[str] = []
        seen: set[str] = set()

        def add_hint(text: str, *, primary: bool) -> None:
            candidate = str(text or "").strip()
            if not candidate:
                return
            normalized = _normalize_diacritic_text(candidate)
            if not normalized or normalized in seen:
                return
            seen.add(normalized)
            extended_hints.append(candidate)
            if primary:
                primary_hints.append(candidate)

        for row in self._score_rows_by_key.values():
            add_hint(row.student_name, primary=True)
        for row in self._score_rows_by_key.values():
            parts = row.student_name.split()
            if len(parts) >= 2:
                add_hint(" ".join(parts[-2:]), primary=True)
        for row in self._score_rows_by_key.values():
            parts = row.student_name.split()
            if parts:
                add_hint(parts[-1], primary=False)

        # Thêm biệt danh làm primary hints (ưu tiên cao nhất)
        for student_name, aliases in self._student_aliases.items():
            for alias in aliases:
                add_hint(alias, primary=True)
                # Thêm combo "alias + điểm" vào extended hints
                short_scores_alias = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"]
                for s in short_scores_alias:
                    add_hint(f"{alias} {s}", primary=False)

        score_hints = [
            "điểm", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10",
            "0.5", "1.5", "2.5", "3.5", "4.5", "5.5", "6.5", "7.5", "8.5", "9.5",
            "không", "một", "hai", "ba", "bốn", "tư", "năm", "sáu", "bảy", "tám", "chín", "mười",
            "rưỡi", "phẩy", "xóa", "hủy",
        ]
        for hint in score_hints:
            add_hint(hint, primary=True)

        short_scores = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"]
        for row in self._score_rows_by_key.values():
            parts = row.student_name.split()
            short_name = " ".join(parts[-2:]) if len(parts) >= 2 else (parts[-1] if parts else "")
            if short_name:
                for s in short_scores:
                    add_hint(f"{short_name} {s}", primary=False)
                    if len(extended_hints) >= VOICE_HINT_EXTENDED_LIMIT:
                        break
            if len(extended_hints) >= VOICE_HINT_EXTENDED_LIMIT:
                break

        self._voice_hints_primary = primary_hints[:VOICE_HINT_PRIMARY_LIMIT]
        self._voice_hints_extended = extended_hints[:VOICE_HINT_EXTENDED_LIMIT]
        self._voice_hints = list(self._voice_hints_extended)

    def _build_smart_voice_hints(self, roster_revision: int | None = None) -> list[str]:
        """Builds a compact phrase-hint list focused on the current context.

        PERF #4: thay vì gửi 96 tên mỗi request, chỉ gửi ≤ VOICE_HINT_SMART_LIMIT:
            1. Tên đang pending (xác suất cao nhất sẽ được nói tiếp).
            2. Tên dùng gần đây (LRU).
            3. Tên còn READY chưa nhập điểm.
            4. Bộ token điểm (số chữ + số digit).
        Lý do: payload phrase nhỏ → Google trả top-1 chính xác hơn, response gọn hơn.
        """
        ordered: list[str] = []
        seen_keys: set[str] = set()

        def add(value: str) -> None:
            candidate = str(value or "").strip()
            if not candidate:
                return
            key = _normalize_diacritic_text(candidate)
            if not key or key in seen_keys:
                return
            seen_keys.add(key)
            ordered.append(candidate)

        # 1. Pending row (rất cao xác suất).
        try:
            pending_key, _at, _rev = self._valid_voice_pending_snapshot(roster_revision=roster_revision)
        except Exception:
            pending_key = None
        with self._score_data_lock:
            if pending_key:
                pending_row = self._score_rows_by_key.get(pending_key)
                if pending_row and pending_row.student_name:
                    add(pending_row.student_name)
                    parts = pending_row.student_name.split()
                    if len(parts) >= 2:
                        add(" ".join(parts[-2:]))
            # 2. LRU tên dùng gần đây.
            for name in list(self._voice_recent_names):
                add(name)
                parts = name.split()
                if len(parts) >= 2:
                    add(" ".join(parts[-2:]))
            # 3. Tên còn READY (chưa pending) — ưu tiên những row chưa có pending_score
            ready_rows = [
                row
                for row in self._score_rows_by_key.values()
                if not row.pending_score and not row.current_score
            ]
            for row in ready_rows:
                if len(ordered) >= VOICE_HINT_SMART_LIMIT - 8:
                    break
                add(row.student_name)
            # 4. Nếu còn slot, thêm tên có current_score nhưng chưa pending (vẫn có thể được sửa)
            for row in self._score_rows_by_key.values():
                if len(ordered) >= VOICE_HINT_SMART_LIMIT - 8:
                    break
                if row.pending_score:
                    continue
                add(row.student_name)

        # 5. Bộ token điểm cố định (cần để Google bắt đúng số chữ).
        for token in (
            "không", "một", "hai", "ba", "bốn", "tư",
            "năm", "sáu", "bảy", "tám", "chín", "mười",
            "phẩy", "rưỡi", "điểm",
        ):
            if len(ordered) >= VOICE_HINT_SMART_LIMIT:
                break
            add(token)
        return ordered[:VOICE_HINT_SMART_LIMIT]

    def _ensure_voice_recognize_executor(self) -> ThreadPoolExecutor:
        """Returns a 2-worker pool for parallel Google Speech requests."""
        with self._voice_executor_lock:
            executor = self._voice_recognize_executor
            if executor is None:
                executor = ThreadPoolExecutor(
                    max_workers=2,
                    thread_name_prefix="VoiceRecog",
                )
                self._voice_recognize_executor = executor
            return executor

    def _shutdown_voice_recognize_executor(self) -> None:
        with self._voice_executor_lock:
            executor = self._voice_recognize_executor
            self._voice_recognize_executor = None
        if executor is not None:
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                # Python <3.9 chưa có cancel_futures
                executor.shutdown(wait=False)
            except Exception:
                pass

    def _require_voice_ready(self) -> None:
        if np is None or sd is None:
            raise RuntimeError("Thiếu thư viện audio. Cần cài: pip install sounddevice numpy")
        if sr is None:
            raise RuntimeError("Thiếu speech_recognition. Cần cài: pip install SpeechRecognition")
        if not self._score_rows_by_key:
            raise RuntimeError("Chưa có danh sách học sinh. Hãy chọn cột điểm đích trước.")

    def _voice_roster_revision(self, roster_revision: int | None = None) -> int:
        """Returns the roster revision that one voice operation must stay bound to."""
        return self._ptt_roster_revision if roster_revision is None else roster_revision

    def _clear_voice_pending(self) -> None:
        """Clears the name-only voice pending state under its own lock."""
        with self._voice_state_lock:
            self._voice_pending_row_key = None
            self._voice_pending_at = 0.0
            self._voice_pending_roster_revision = 0

    def _clear_voice_pending_if_current(self, row_key: str, pending_at: float, roster_revision: int) -> None:
        """Clears pending state only if another voice pass has not replaced it."""
        with self._voice_state_lock:
            if (
                self._voice_pending_row_key == row_key
                and self._voice_pending_at == pending_at
                and self._voice_pending_roster_revision == roster_revision
            ):
                self._voice_pending_row_key = None
                self._voice_pending_at = 0.0
                self._voice_pending_roster_revision = 0

    def _set_voice_pending(self, row_key: str, roster_revision: int | None = None) -> None:
        """Stores one name-only voice match for the matching roster revision."""
        with self._voice_state_lock:
            self._voice_pending_row_key = row_key
            self._voice_pending_at = time.perf_counter()
            self._voice_pending_roster_revision = self._voice_roster_revision(roster_revision)
            # BUG #2 FIX: Pending row mới → tied list cũ không còn áp dụng được.
            #   Tránh trường hợp user nói "Trâm" (tied), rồi nói "An" (pending An),
            #   rồi nói "một 8" — sẽ pick từ tied cũ thay vì điểm 1 cho An.
            self._voice_tied_candidates = []
            self._voice_tied_at = 0.0
            self._voice_tied_revision = 0

    def _valid_voice_pending_snapshot(self, roster_revision: int | None = None) -> tuple[str | None, float, int]:
        """Returns a pending voice row only when it belongs to the active roster revision."""
        expected_revision = self._voice_roster_revision(roster_revision)
        now = time.perf_counter()
        with self._voice_state_lock:
            pending_key = self._voice_pending_row_key
            pending_at = self._voice_pending_at
            pending_revision = self._voice_pending_roster_revision
            if not pending_key:
                return None, 0.0, expected_revision
            if pending_revision != expected_revision or (now - pending_at) > VOICE_PENDING_TIMEOUT:
                self._voice_pending_row_key = None
                self._voice_pending_at = 0.0
                self._voice_pending_roster_revision = 0
                return None, 0.0, expected_revision
            return pending_key, pending_at, pending_revision

    # ------------------------------------------------------------------
    # KHMER #B — Voice picker state for tied phonetic candidates
    # ------------------------------------------------------------------

    def _clear_voice_tied_candidates(self) -> None:
        """Resets the tied-candidates picker state."""
        with self._voice_state_lock:
            self._voice_tied_candidates = []
            self._voice_tied_at = 0.0
            self._voice_tied_revision = 0

    def _set_voice_tied_candidates(
        self,
        row_keys: list[str],
        roster_revision: int | None = None,
    ) -> None:
        """Stores a tied-candidates list for the next picker command.

        Khi `_match_student` thấy ≥ 2 row cùng phonetic không phân biệt được,
        gọi hàm này để PTT lệnh tiếp theo (vd "một"/"hai" + điểm) chọn được
        đúng row. Mỗi lần ghi đè list cũ để tránh state cũ tồn đọng.
        """
        cleaned_keys = [str(k).strip() for k in row_keys if str(k).strip()]
        if not cleaned_keys:
            return
        with self._voice_state_lock:
            self._voice_tied_candidates = list(cleaned_keys)
            self._voice_tied_at = time.perf_counter()
            self._voice_tied_revision = self._voice_roster_revision(roster_revision)
            # Tied list active → clear pending row để tránh xung đột.
            self._voice_pending_row_key = None
            self._voice_pending_at = 0.0
            self._voice_pending_roster_revision = 0

    def _valid_voice_tied_snapshot(
        self,
        roster_revision: int | None = None,
    ) -> tuple[list[str], float, int]:
        """Returns the active tied-candidates list (or empty if expired/stale)."""
        expected_revision = self._voice_roster_revision(roster_revision)
        now = time.perf_counter()
        with self._voice_state_lock:
            if not self._voice_tied_candidates:
                return [], 0.0, expected_revision
            if (
                self._voice_tied_revision != expected_revision
                or (now - self._voice_tied_at) > VOICE_PENDING_TIMEOUT
            ):
                self._voice_tied_candidates = []
                self._voice_tied_at = 0.0
                self._voice_tied_revision = 0
                return [], 0.0, expected_revision
            return list(self._voice_tied_candidates), self._voice_tied_at, self._voice_tied_revision

    # ---- Beep sound helpers ----

    def _play_beep_sequence(self, pattern: Sequence[tuple[int, int, int]], event_name: str) -> None:
        """Plays one named audio feedback pattern without blocking the UI thread."""
        if winsound is None:
            return

        def _run_sequence() -> None:
            try:
                for frequency, duration_ms, pause_ms in pattern:
                    if frequency > 0 and duration_ms > 0:
                        winsound.Beep(frequency, duration_ms)
                    if pause_ms > 0:
                        time.sleep(pause_ms / 1000.0)
            except Exception as error:  # noqa: BLE001 - fallback for Windows audio devices
                try:
                    winsound.MessageBeep()
                except Exception as fallback_error:  # noqa: BLE001
                    if not getattr(self, "_beep_failure_logged", False):
                        self._beep_failure_logged = True
                        self._log(
                            f"Không phát được âm báo {event_name}: {error}; fallback: {fallback_error}",
                            tag=LogTag.WARNING,
                        )

        threading.Thread(target=_run_sequence, daemon=True, name=f"VnEduBeep-{event_name}").start()

    def _play_beep(self, frequency: int = 1000, duration_ms: int = 100) -> None:
        """Phát tiếng beep không chặn GUI thread (chạy trong daemon thread)."""
        self._play_beep_sequence(((frequency, duration_ms, 0),), "beep")

    def _flash_recording_feedback(self) -> None:
        """Shows recording start visually without injecting sound into the microphone."""
        if not hasattr(self, "btn_ptt_toggle"):
            return
        def restore_button_color() -> None:
            if not self._ptt_enabled or not hasattr(self, "btn_ptt_toggle"):
                return
            try:
                self.btn_ptt_toggle.config(bg=APP_SUCCESS, activebackground="#15803d")
                self.btn_ptt_toggle._hover_prev_bg = APP_SUCCESS  # type: ignore[attr-defined]
            except tk.TclError:
                pass

        try:
            self.btn_ptt_toggle.config(bg=APP_DANGER, activebackground="#b91c1c")
            self.btn_ptt_toggle._hover_prev_bg = APP_DANGER  # type: ignore[attr-defined]
            self.root.after(140, restore_button_color)
        except tk.TclError:
            pass

    def _beep_recording_start(self) -> None:
        """Start-recording feedback: visual only, no audio contamination."""
        self._flash_recording_feedback()

    def _beep_uncertain_result(self) -> None:
        """Low tone for a processed transcript that is not safe to auto-apply."""
        self._play_beep_sequence(((620, 90, 0),), "chưa chắc")

    def _beep_needs_confirmation(self) -> None:
        """Distinct two-tone prompt for name-only/conflict confirmation states."""
        self._play_beep_sequence(((880, 70, 50), (660, 110, 0)), "cần xác nhận")

    def _beep_voice_error(self) -> None:
        """Warning tone for audio/STT errors."""
        self._play_beep_sequence(((420, 150, 70), (420, 150, 0)), "lỗi voice")

    def _beep_score_accepted(self) -> None:
        """Tiếng beep xác nhận đã ghi điểm vào hàng chờ."""
        self._play_beep_sequence(((1000, 70, 45), (1400, 90, 0)), "ghi thành công")

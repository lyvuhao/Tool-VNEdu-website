"""Nhận dạng giọng nói và chọn transcript tốt nhất."""

from __future__ import annotations

import math
import time
from concurrent.futures import FIRST_COMPLETED, wait as futures_wait
from typing import Any

from ..compat import np, sr
from ..config import AUDIO_SAMPLE_RATE
from ..models import LogTag, VoiceMatchResult
from ..voice.constants import (
    _VOICE_UNDO_PATTERN,
    VOICE_BOOST_FORCE_MAX_AMP,
    VOICE_BOOST_RETRY_GAIN_DB,
    VOICE_BOOST_TRIGGER_MAX_AMP,
    VOICE_COMMAND_SPACE_PATTERN,
    VOICE_FAST_ACCEPT_SCORE,
    VOICE_FILLER_PATTERN,
    VOICE_RECOGNIZE_PARALLEL_TIMEOUT,
    VOICE_SCORE_DECIMAL_PATTERN,
    VOICE_SCORE_WORD_PATTERN,
    VOICE_TRANSCRIPT_OVERRIDE_MARGIN,
)
from ..voice.google_speech import _voice_recognize_google_session
from ..voice.phonetics import _normalize_diacritic_text


class VoiceRecognitionMixin:
    """Nhận dạng giọng nói và chọn transcript tốt nhất."""

    def _process_ptt_audio_task(self, request_id: int, roster_revision: int, audio_data: Any, _started_at: float) -> None:
        if np is None or sr is None or self._recognizer is None:
            self._dispatch_ptt_result(
                request_id,
                roster_revision,
                lambda: (self._beep_voice_error(), self.voice_status_var.set("❌ Thiếu thư viện nhận dạng.")),
            )
            return
        try:
            audio_float = audio_data.flatten().astype(np.float32)
            duration = len(audio_float) / AUDIO_SAMPLE_RATE
            if duration < 0.25:
                # IMP-C1: Hiện thời lượng cụ thể để user biết cần giữ lâu hơn bao nhiêu
                dur_ms = int(duration * 1000)
                self._dispatch_ptt_result(
                    request_id,
                    roster_revision,
                    lambda _d=dur_ms: (
                        self._beep_voice_error(),
                        self.voice_status_var.set(f"⚠️ Quá ngắn ({_d}ms). Hãy giữ Space ≥ 250ms."),
                    ),
                )
                return
            max_amp = float(np.max(np.abs(audio_float)))
            if max_amp < 0.003:
                # IMP-C1: Hiện cường độ tín hiệu cụ thể để user biết mức hiện tại
                db_val = round(20 * math.log10(max_amp + 1e-10), 1)
                self._dispatch_ptt_result(
                    request_id,
                    roster_revision,
                    lambda _db=db_val: (
                        self._beep_voice_error(),
                        self.voice_status_var.set(f"⚠️ Tín hiệu quá yếu ({_db}dB). Nói gần micro hơn."),
                    ),
                )
                return
            trimmed_audio = self._trim_ptt_audio(audio_float)

            # PERF #5: chỉ dùng "boosted" khi tín hiệu yếu thật sự; dùng "raw" còn lại.
            #          → cắt 1 attempt cho 80% case bình thường.
            primary_audio = self._enhance_audio_for_recognition(trimmed_audio)
            if max_amp < VOICE_BOOST_FORCE_MAX_AMP:
                # Tín hiệu cực yếu — boost luôn cho secondary (hơn raw).
                secondary_audio = self._enhance_audio_for_recognition(
                    trimmed_audio,
                    boost_db=VOICE_BOOST_RETRY_GAIN_DB,
                )
                secondary_label = "boosted"
            elif max_amp < VOICE_BOOST_TRIGGER_MAX_AMP:
                # Tín hiệu hơi yếu — vẫn ưu tiên raw, để boost làm "tertiary" sau.
                secondary_audio = trimmed_audio
                secondary_label = "raw"
            else:
                secondary_audio = trimmed_audio
                secondary_label = "raw"

            # PERF #4: smart hints chỉ chứa context hiện tại (≤ 30 phrase) — Google trả top-1 chuẩn hơn.
            smart_hints = self._build_smart_voice_hints(roster_revision=roster_revision) if (
                self._recognizer_phrase_list_supported is not False
            ) else None

            # PERF #3: pre-encode FLAC một lần (libsndfile in-process) — bỏ qua flac.exe.
            primary_audio_obj, primary_flac, primary_sr = self._audio_to_recognition_payload(primary_audio)
            if secondary_audio is primary_audio:
                secondary_audio_obj = primary_audio_obj
                secondary_flac = primary_flac
                secondary_sr = primary_sr
            else:
                secondary_audio_obj, secondary_flac, secondary_sr = self._audio_to_recognition_payload(secondary_audio)

            # PERF #1: chạy song song 2 attempt (primary có hint + secondary không hint).
            #          Future nào về trước có match đủ tốt thì cancel cái còn lại.
            executor = self._ensure_voice_recognize_executor()

            def attempt_primary() -> tuple[VoiceMatchResult | None, str, str]:
                match, text = self._recognize_transcripts(
                    primary_audio_obj,
                    roster_revision=roster_revision,
                    phrase_hints=smart_hints,
                    allow_no_hint_retry=False,
                    flac_bytes=primary_flac,
                    sample_rate=primary_sr,
                )
                return match, text, "enhanced+hint"

            def attempt_secondary() -> tuple[VoiceMatchResult | None, str, str]:
                match, text = self._recognize_transcripts(
                    secondary_audio_obj,
                    roster_revision=roster_revision,
                    phrase_hints=None,
                    allow_no_hint_retry=False,
                    flac_bytes=secondary_flac,
                    sample_rate=secondary_sr,
                )
                return match, text, secondary_label

            primary_future = executor.submit(attempt_primary)
            secondary_future = executor.submit(attempt_secondary)
            futures = {primary_future, secondary_future}

            best_match: VoiceMatchResult | None = None
            best_text = ""
            best_attempt_label = ""
            best_rank = -1
            primary_done = False
            secondary_done = False
            primary_error: BaseException | None = None
            secondary_error: BaseException | None = None
            remaining_timeout = VOICE_RECOGNIZE_PARALLEL_TIMEOUT
            wait_started_at = time.perf_counter()
            while futures:
                done, _pending = futures_wait(
                    futures,
                    timeout=max(0.05, remaining_timeout),
                    return_when=FIRST_COMPLETED,
                )
                if not done:
                    # Hết thời gian — huỷ phần còn lại để không treo PTT worker.
                    for fut in futures:
                        fut.cancel()
                    break
                for fut in done:
                    futures.discard(fut)
                    try:
                        attempt_match, attempt_text, attempt_label = fut.result()
                    except Exception as recog_error:  # noqa: BLE001
                        if fut is primary_future:
                            primary_error = recog_error
                            primary_done = True
                        else:
                            secondary_error = recog_error
                            secondary_done = True
                        continue
                    if fut is primary_future:
                        primary_done = True
                    else:
                        secondary_done = True
                    if attempt_match is not None:
                        rank = self._voice_match_rank(attempt_match, attempt_text)
                        if rank > best_rank:
                            best_match = attempt_match
                            best_text = attempt_text
                            best_attempt_label = attempt_label
                            best_rank = rank
                        # Match đủ chắc chắn → huỷ tiếp, không cần chờ kết quả còn lại.
                        if attempt_match.match_score >= VOICE_FAST_ACCEPT_SCORE:
                            for other in futures:
                                other.cancel()
                            futures.clear()
                            break
                    elif attempt_text and not best_text:
                        best_text = attempt_text
                        best_attempt_label = attempt_label
                if not futures:
                    break
                remaining_timeout = VOICE_RECOGNIZE_PARALLEL_TIMEOUT - (time.perf_counter() - wait_started_at)
                if remaining_timeout <= 0.05:
                    for fut in futures:
                        fut.cancel()
                    break

            # Nếu cả hai attempt cùng raise (cùng do mạng), surface lỗi để user biết.
            if best_match is None and not best_text and primary_done and secondary_done and primary_error and secondary_error:
                raise primary_error

            # Nếu chỉ 1 attempt raise (mạng chập), log để dễ chẩn đoán nhưng không fail vội.
            if primary_error is not None and secondary_error is None:
                self._log(f"PTT primary error: {primary_error}", tag=LogTag.WARNING)
            elif secondary_error is not None and primary_error is None:
                self._log(f"PTT secondary error: {secondary_error}", tag=LogTag.WARNING)

            if best_match is not None and best_attempt_label not in ("enhanced+hint", "enhanced"):
                self._log(f"PTT secondary {best_attempt_label}: nhận dạng thành công.", tag=LogTag.INFO)

            # PERF #5 (tertiary): chỉ chạy thêm "boosted" khi cả 2 attempt cùng fail và
            # tín hiệu KHÔNG quá yếu (đã không boost ở primary). Tránh phí 1 RTT trong
            # 80% case. Khi tín hiệu cực yếu thì boosted đã làm secondary rồi.
            if (
                best_match is None
                and not best_text
                and VOICE_BOOST_FORCE_MAX_AMP <= max_amp < VOICE_BOOST_TRIGGER_MAX_AMP
            ):
                tertiary_audio = self._enhance_audio_for_recognition(
                    trimmed_audio,
                    boost_db=VOICE_BOOST_RETRY_GAIN_DB,
                )
                tertiary_audio_obj, tertiary_flac, tertiary_sr = self._audio_to_recognition_payload(tertiary_audio)
                try:
                    tert_match, tert_text = self._recognize_transcripts(
                        tertiary_audio_obj,
                        roster_revision=roster_revision,
                        phrase_hints=None,
                        allow_no_hint_retry=False,
                        flac_bytes=tertiary_flac,
                        sample_rate=tertiary_sr,
                    )
                except Exception:  # noqa: BLE001
                    tert_match, tert_text = None, ""
                if tert_match is not None:
                    best_match = tert_match
                    best_text = tert_text
                    best_attempt_label = "boosted"
                    self._log("PTT tertiary boosted: nhận dạng thành công.", tag=LogTag.INFO)
                elif tert_text and not best_text:
                    best_text = tert_text
                    best_attempt_label = "boosted"

            # PERF #1b: fallback recognize không show_all để có ít nhất 1 transcript text
            # khi cả 2 attempt parallel không trả alternative nào. Chỉ chạy khi thật sự cần
            # (tránh +1 RTT cho case match thành công ở trên).
            if best_match is None and not best_text:
                try:
                    fallback_text = self._recognize_google_fallback_text(
                        primary_audio_obj,
                        flac_bytes=primary_flac,
                        sample_rate=primary_sr,
                    )
                except Exception:  # noqa: BLE001
                    fallback_text = ""
                if fallback_text:
                    fallback_match, _msg = self._resolve_voice_command(
                        fallback_text,
                        roster_revision=roster_revision,
                        dry_run=True,  # BUG #10 FIX: probe trước, commit sau khi xác nhận thắng
                    )
                    if fallback_match is not None:
                        best_match = fallback_match
                    best_text = fallback_text
                    best_attempt_label = "fallback_text"

            if best_match is None and best_text:
                undo_cleaned = self._preprocess_voice_text(best_text)
                if _VOICE_UNDO_PATTERN.search(undo_cleaned):
                    self._dispatch_ptt_result(
                        request_id,
                        roster_revision,
                        lambda: (self._on_undo_shortcut(), self.voice_status_var.set("↩️ Đã undo lệnh trước.")),
                    )
                    return
            if best_match is None:
                # BUG #10 FIX: Probe pipeline chạy `dry_run=True` nên tied/pending
                # state KHÔNG được set. Nếu best_text non-empty và transcript
                # thắng có khả năng gây tied/name_only, re-resolve với
                # dry_run=False để commit state cho UI status đúng.
                if best_text:
                    try:
                        self._resolve_voice_command(
                            best_text,
                            roster_revision=roster_revision,
                            dry_run=False,
                        )
                    except Exception:  # noqa: BLE001
                        pass
                # KHMER #B: tied list active sau khi resolve fail → hướng dẫn
                # user nói "một"/"hai" để chọn. Ưu tiên trên cả pending/no-match
                # message vì state này yêu cầu input rất cụ thể.
                tied_keys, _at, _rev = self._valid_voice_tied_snapshot(
                    roster_revision=roster_revision
                )
                if tied_keys:
                    with self._score_data_lock:
                        tied_names = [
                            self._score_rows_by_key[k].student_name
                            for k in tied_keys
                            if k in self._score_rows_by_key
                        ]
                    if tied_names:
                        labelled = "  •  ".join(
                            f"{i + 1}. {name}" for i, name in enumerate(tied_names[:5])
                        )
                        suggestion_token = "/".join(
                            ["một", "hai", "ba", "bốn", "năm"][: len(tied_names)]
                        )
                        self._dispatch_ptt_result(
                            request_id,
                            roster_revision,
                            lambda lbl=labelled, sug=suggestion_token, names=list(tied_names): (
                                self._beep_needs_confirmation(),
                                self.voice_status_var.set(
                                    f"🔢 Trùng tên: {lbl} — nói '{sug}' rồi điểm."
                                ),
                                self._log(
                                    f"PTT TIED ({len(names)} HS): {', '.join(names)}",
                                    tag=LogTag.WARNING,
                                ),
                            ),
                        )
                        return
                pending_key, _pending_at, _pending_revision = self._valid_voice_pending_snapshot(
                    roster_revision=roster_revision
                )
                # THREAD-SAFETY: đọc _score_rows_by_key từ PTT worker — cần lock
                with self._score_data_lock:
                    pending_row = self._score_rows_by_key.get(pending_key) if pending_key else None
                if pending_row is not None:
                    self._dispatch_ptt_result(
                        request_id,
                        roster_revision,
                        lambda name=pending_row.student_name, key=pending_key: (
                            self._beep_needs_confirmation(),
                            self.voice_status_var.set(f"🎯 Nghe '{name}' — nói tiếp điểm số..."),
                            self._log(f"PTT name-only: '{name}'", tag=LogTag.WARNING),
                            self._highlight_pending_row(key),
                        ),
                    )
                elif best_text:
                    self._dispatch_ptt_result(
                        request_id,
                        roster_revision,
                        lambda text=best_text: (
                            self._beep_uncertain_result(),
                            self.voice_status_var.set(f"⚠️ Nghe '{text}' nhưng chưa ghép được học sinh."),
                            self._log(f"PTT không match: '{text}'", tag=LogTag.WARNING),
                        ),
                    )
                else:
                    self._dispatch_ptt_result(
                        request_id,
                        roster_revision,
                        lambda: (
                            self._beep_voice_error(),
                            self.voice_status_var.set("⚠️ Không nhận dạng được. Hãy thử lại."),
                        ),
                    )
                return
            # BUG #10 FIX: Trước khi commit best_match qua UI, re-resolve transcript
            # thắng với dry_run=False để side-effect state (clear pending/tied,
            # cập nhật cache) đúng với transcript được apply. Tránh trường hợp
            # transcript probe sau cùng overwrite state, hoặc state không được
            # commit khi transcript thắng nằm giữa list alternative.
            if best_text:
                try:
                    self._resolve_voice_command(
                        best_text,
                        roster_revision=roster_revision,
                        dry_run=False,
                    )
                except Exception:  # noqa: BLE001
                    pass
            self._dispatch_ptt_result(request_id, roster_revision, lambda match=best_match: self._apply_voice_match(match))
        except Exception as error:  # noqa: BLE001
            self._dispatch_ptt_result(
                request_id,
                roster_revision,
                lambda err=error: (
                    self._beep_voice_error(),
                    self.voice_status_var.set(f"❌ Lỗi xử lý giọng nói: {err}"),
                ),
            )

    def _recognize_google_candidates(
        self,
        audio_obj: Any,
        phrase_hints: list[str] | None = None,
        *,
        flac_bytes: bytes | None = None,
        sample_rate: int | None = None,
    ) -> list[tuple[str, float]]:
        transcripts: list[tuple[str, float]] = []
        raw: Any = None
        hint_list = list(phrase_hints or [])
        # PERF #2: ưu tiên gọi qua HTTPS session keep-alive (nếu có `requests`).
        # PERF #3: truyền `flac_bytes` đã encode sẵn để bỏ qua flac.exe.
        # Nếu hint không được hỗ trợ thì gọi không hint, sr.UnknownValueError → trả [].
        try:
            if hint_list and self._recognizer_phrase_list_supported is not False:
                try:
                    raw = _voice_recognize_google_session(
                        self._recognizer,
                        audio_obj,
                        language="vi-VN",
                        show_all=True,
                        phrase_hints=hint_list,
                    )
                    self._recognizer_phrase_list_supported = True
                except TypeError:
                    self._recognizer_phrase_list_supported = False
                    raw = _voice_recognize_google_session(
                        self._recognizer,
                        audio_obj,
                        language="vi-VN",
                        show_all=True,
                        flac_bytes=flac_bytes,
                        sample_rate_override=sample_rate,
                    )
            else:
                raw = _voice_recognize_google_session(
                    self._recognizer,
                    audio_obj,
                    language="vi-VN",
                    show_all=True,
                    flac_bytes=flac_bytes,
                    sample_rate_override=sample_rate,
                )
        except sr.UnknownValueError:
            return transcripts
        except sr.RequestError as error:
            raise RuntimeError(f"Lỗi kết nối Google: {error}") from error

        if isinstance(raw, dict):
            alternatives = raw.get("alternative", [])
            if not alternatives:
                for result_entry in raw.get("result", []):
                    if isinstance(result_entry, dict):
                        alternatives = result_entry.get("alternative", [])
                        if alternatives:
                            break
            candidate_entries: list[tuple[str, float, int]] = []
            for index, alternative in enumerate(alternatives[:6]):
                transcript = str(alternative.get("transcript", "")).strip()
                if not transcript:
                    continue
                confidence_value = alternative.get("confidence", 0.0)
                try:
                    confidence = float(confidence_value)
                except (TypeError, ValueError):
                    confidence = 0.0
                candidate_entries.append((transcript, confidence, index))
            candidate_entries.sort(key=lambda item: (item[1], -item[2]), reverse=True)
            seen_transcripts: set[str] = set()
            for transcript, confidence, _index in candidate_entries:
                if transcript in seen_transcripts:
                    continue
                seen_transcripts.add(transcript)
                transcripts.append((transcript, confidence))
        elif isinstance(raw, str) and raw.strip():
            transcripts.append((raw.strip(), 0.0))
        return transcripts

    def _recognize_google_fallback_text(
        self,
        audio_obj: Any,
        *,
        flac_bytes: bytes | None = None,
        sample_rate: int | None = None,
    ) -> str:
        try:
            fallback = _voice_recognize_google_session(
                self._recognizer,
                audio_obj,
                language="vi-VN",
                show_all=False,
                flac_bytes=flac_bytes,
                sample_rate_override=sample_rate,
            )
        except sr.UnknownValueError:
            return ""
        except sr.RequestError as error:
            raise RuntimeError(f"Lỗi kết nối Google: {error}") from error
        except Exception as error:
            raise RuntimeError(f"Lỗi Google fallback: {error}") from error
        return fallback.strip() if isinstance(fallback, str) else ""

    def _voice_match_rank(self, match: VoiceMatchResult, transcript: str, confidence: float = 0.0) -> int:
        normalized_length = len(_normalize_diacritic_text(transcript))
        confidence_bonus = int(max(0.0, min(1.0, confidence)) * 100)
        return (match.match_score * 1000) + (confidence_bonus * 10) + normalized_length

    def _best_voice_match_from_transcripts(
        self,
        transcripts: list[object],
        *,
        stop_score: int = VOICE_FAST_ACCEPT_SCORE,
        roster_revision: int | None = None,
    ) -> tuple[VoiceMatchResult | None, str]:
        # V6 FIX: `transcripts` đã được sort theo thứ tự ưu tiên của Google
        # (confidence desc → index asc), nên ứng viên ĐẦU TIÊN khớp được là phán
        # đoán âm học tốt nhất. Một ứng viên xếp sau chỉ được phép qua mặt khi
        # điểm fuzzy cao hơn ít nhất VOICE_TRANSCRIPT_OVERRIDE_MARGIN, hoặc khi nó
        # có confidence cao hơn rõ rệt mà điểm fuzzy không thấp hơn. Điều này
        # tránh để nhiễu fuzzy 1 điểm lật ngược thứ tự của Google.
        best_match: VoiceMatchResult | None = None
        best_match_score = -1
        best_confidence = -1.0
        best_text = ""
        if transcripts:
            first_candidate = transcripts[0]
            if isinstance(first_candidate, tuple):
                best_text = str(first_candidate[0]).strip()
            else:
                best_text = str(first_candidate).strip()
        for candidate in transcripts:
            if isinstance(candidate, tuple):
                transcript = str(candidate[0]).strip()
                try:
                    confidence = float(candidate[1])
                except (TypeError, ValueError):
                    confidence = 0.0
            else:
                transcript = str(candidate).strip()
                confidence = 0.0
            if not transcript:
                continue
            match_result, _message = self._resolve_voice_command(
                transcript,
                roster_revision=roster_revision,
                dry_run=True,  # BUG #10 FIX: probe mode — không commit state khi xếp hạng
            )
            if match_result is None:
                continue
            should_override = (
                best_match is None
                or (match_result.match_score - best_match_score) >= VOICE_TRANSCRIPT_OVERRIDE_MARGIN
                or (confidence > best_confidence + 1e-6 and match_result.match_score >= best_match_score)
            )
            if should_override:
                best_match = match_result
                best_match_score = match_result.match_score
                best_confidence = confidence
                best_text = transcript
            if best_match is not None and best_match.match_score >= stop_score:
                break
        return best_match, best_text

    def _recognize_transcripts(
        self,
        audio_obj: Any,
        *,
        roster_revision: int | None = None,
        phrase_hints: list[str] | None = None,
        allow_no_hint_retry: bool = True,
        flac_bytes: bytes | None = None,
        sample_rate: int | None = None,
    ) -> tuple[VoiceMatchResult | None, str]:
        """Runs one Google Speech request and matches a student command.

        Args:
            audio_obj: sr.AudioData đã encode (FLAC/LINEAR16) sẵn sàng gửi.
            roster_revision: chống stale matching khi roster đã đổi.
            phrase_hints: danh sách hint ưu tiên cho lần đầu. Khi None, đoán theo
                bối cảnh hiện tại bằng `_build_smart_voice_hints`.
            allow_no_hint_retry: nếu True (legacy), khi attempt-có-hint fail thì
                thử lại không hint trong CÙNG hàm. Pipeline parallel mới đặt False
                vì vòng song song bên ngoài đã đảm trách attempt no-hint.
            flac_bytes / sample_rate: PERF #3 — FLAC bytes đã pre-encode bằng
                libsndfile, kèm sample rate gốc. Khi truyền vào, session helper
                bỏ qua flac.exe của sr (~70–120ms/clip).
        """
        attempt_specs: list[list[str] | None] = []
        # Lần 1: hint (ưu tiên smart hints).
        if self._recognizer_phrase_list_supported is not False:
            if phrase_hints is None:
                resolved_hints = self._build_smart_voice_hints(roster_revision=roster_revision)
            else:
                resolved_hints = list(phrase_hints)
            if resolved_hints:
                attempt_specs.append(resolved_hints)
        # Lần 2 (legacy): no-hint retry, chỉ chạy khi caller cho phép.
        if allow_no_hint_retry or not attempt_specs:
            attempt_specs.append(None)

        best_match: VoiceMatchResult | None = None
        best_text = ""
        best_rank = -1
        for hint_list in attempt_specs:
            transcripts = self._recognize_google_candidates(
                audio_obj,
                phrase_hints=hint_list,
                flac_bytes=flac_bytes,
                sample_rate=sample_rate,
            )
            if not transcripts:
                continue
            attempt_match, attempt_text = self._best_voice_match_from_transcripts(
                transcripts,
                roster_revision=roster_revision,
            )
            if attempt_match is not None:
                attempt_rank = self._voice_match_rank(attempt_match, attempt_text)
                if attempt_rank > best_rank:
                    best_rank = attempt_rank
                    best_match = attempt_match
                    best_text = attempt_text
                if attempt_match.match_score >= VOICE_FAST_ACCEPT_SCORE:
                    return attempt_match, attempt_text
            elif not best_text:
                first_candidate = transcripts[0]
                if isinstance(first_candidate, tuple):
                    best_text = str(first_candidate[0]).strip()
                else:
                    best_text = str(first_candidate).strip()

        if (
            allow_no_hint_retry
            and best_match is None
            and not best_text
        ):
            fallback_text = self._recognize_google_fallback_text(
                audio_obj,
                flac_bytes=flac_bytes,
                sample_rate=sample_rate,
            )
            if fallback_text:
                fallback_match, _message = self._resolve_voice_command(
                    fallback_text,
                    roster_revision=roster_revision,
                )
                if fallback_match is not None:
                    return fallback_match, fallback_text
                if not best_text:
                    best_text = fallback_text
        return best_match, best_text

    def _preprocess_voice_text(self, text: str) -> str:
        cleaned = str(text or "").lower().strip()
        cleaned = VOICE_FILLER_PATTERN.sub(" ", cleaned)
        cleaned = cleaned.replace(":", " ").replace(";", " ")
        cleaned = VOICE_SCORE_DECIMAL_PATTERN.sub(r"\1.\2", cleaned)
        cleaned = cleaned.replace(",", " ")
        cleaned = VOICE_SCORE_WORD_PATTERN.sub(" diem ", cleaned)
        cleaned = VOICE_COMMAND_SPACE_PATTERN.sub(" ", cleaned).strip()
        return cleaned

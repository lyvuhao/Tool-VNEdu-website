"""Worker nhập theo lịch (thủ công) và kết thúc phiên.

Logic của worker nằm ở `ScheduleJob` (schedule_job.py); file này giữ điểm vào của thread nền, vòng
poll hàng đợi sự kiện trên UI thread và xử lý khi kết thúc.
"""

from .schedule_job import ScheduleJob


class ScheduleWorkerMixin:
    """Worker nhập theo lịch (thủ công) và kết thúc phiên."""

    def _schedule_worker(self, params):
        """Worker thread schedule với checkpoint resume và báo cáo chi tiết từng slot."""
        ScheduleJob(self, params).run()

    def _poll_schedule_queue(self):
        """Poll schedule queue cho progress updates (chạy trong UI thread)."""
        try:
            while not self._schedule_queue.empty():
                msg = self._schedule_queue.get_nowait()
                msg_type = msg[0]

                if msg_type == "log":
                    self._log(msg[1], msg[2] if len(msg) > 2 else "info")

                elif msg_type == "progress":
                    text = msg[1]
                    count = msg[2] if len(msg) > 2 else 0
                    self.lbl_sched_progress.config(text=f"⏳ {text}")
                    current_total = int(float(self.sched_progressbar["maximum"] or 1))
                    self._set_sched_live_progress(
                        current=count,
                        total=current_total,
                        phase="Schedule",
                        detail=text,
                        state="running",
                    )

                elif msg_type == "progress_total":
                    try:
                        self.sched_progressbar["maximum"] = max(
                            int(msg[1]),
                            int(float(self.sched_progressbar["maximum"] or 1)),
                        )
                    except Exception:
                        pass

                elif msg_type == "ppct_sync":
                    self._update_sched_ppct_runtime(
                        last_success=msg[1],
                        next_ppct=msg[2] if len(msg) > 2 else None,
                        status="running",
                    )

                elif msg_type == "slot_result":
                    self._schedule_results.append(msg[1])

                elif msg_type == "checkpoint":
                    self._update_schedule_resume_snapshot(msg[1], persist=True)

                elif msg_type == "error":
                    self._log(msg[1], "error")

                elif msg_type == "done":
                    self._schedule_finished(msg[1])
                    return

        except Exception as e:
            print(f"[SCHED_POLL] Error: {e}")

        if self._schedule_running and self._root_exists():
            self.root.after(150, self._poll_schedule_queue)

    def _schedule_finished(self, summary):
        """Xử lý khi schedule hoàn tất hoặc tạm dừng."""
        completed = int(summary.get("completed", 0) or 0)
        skipped_count = int(summary.get("skipped", 0) or 0)
        error_count = int(summary.get("errors", 0) or 0)
        resume_state = summary.get("resume_state")
        stopped = bool(summary.get("stopped"))
        next_ppct = summary.get("next_ppct")
        last_success_ppct = summary.get("last_success_ppct")

        self._schedule_running = False
        self._update_schedule_resume_snapshot(
            resume_state=resume_state,
            params=self._schedule_resume_params if resume_state else None,
            persist=True,
        )
        self._set_schedule_button_states(
            running=False,
            can_resume=bool(self._schedule_resume_state),
        )
        self._set_auto_login_button_state()

        processed_count = completed + skipped_count + error_count
        if stopped:
            self.sched_progressbar["value"] = min(
                processed_count, self.sched_progressbar["maximum"]
            )
            self.lbl_sched_progress.config(
                text=(
                    f"⏸ Tạm dừng: {completed} nhập, {skipped_count} skip, "
                    f"{error_count} lỗi"
                )
            )
            self._update_sched_ppct_runtime(
                last_success=last_success_ppct,
                next_ppct=next_ppct,
                status="paused",
            )
            self._log(
                f"⏸ Schedule tạm dừng: {completed} thành công, "
                f"{skipped_count} đã có, {error_count} lỗi",
                "warning",
            )
            self._set_sched_live_progress(
                current=processed_count,
                total=int(float(self.sched_progressbar["maximum"] or 1)),
                phase="Schedule",
                detail="Đã tạm dừng an toàn, có thể bấm Tiếp tục từ checkpoint gần nhất",
                state="paused",
            )
        else:
            self.sched_progressbar["value"] = self.sched_progressbar["maximum"]
            self.lbl_sched_progress.config(
                text=(
                    f"✅ Hoàn tất: {completed} nhập, {skipped_count} skip, "
                    f"{error_count} lỗi"
                )
            )
            self._update_sched_ppct_runtime(
                last_success=last_success_ppct,
                next_ppct=next_ppct,
                status="done",
            )
            self._log(
                f"🏁 Schedule hoàn tất: {completed} thành công, "
                f"{skipped_count} đã có, {error_count} lỗi",
                "success" if error_count == 0 else "warning",
            )
            self._set_sched_live_progress(
                current=processed_count,
                total=int(float(self.sched_progressbar["maximum"] or 1)),
                phase="Schedule",
                detail="Đã xử lý hết các slot trong kế hoạch hiện tại",
                state="success" if error_count == 0 else "error",
            )

        pending_items = self._build_pending_schedule_items(
            self._schedule_resume_params,
            self._schedule_resume_state,
        )
        summary_payload = {
            **summary,
            "results": list(self._schedule_results),
            "pending_items": pending_items,
        }
        self._schedule_last_summary = summary_payload
        if completed > 0:
            self._invalidate_class_stats_cache()
            self._on_sched_progress_context_changed()
        if self._closing:
            return
        self._show_schedule_summary(summary_payload)

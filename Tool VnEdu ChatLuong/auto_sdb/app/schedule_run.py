"""Chạy / tiếp tục / dừng nhập theo lịch."""

import copy
import threading
import tkinter as tk
from tkinter import messagebox

from ..config import SCHEDULE_MODE_KHDH, SCHEDULE_MODE_MANUAL


class ScheduleRunMixin:
    """Chạy / tiếp tục / dừng nhập theo lịch."""

    # -----------------------------------------------------------------
    # BATCH LỚP CONTROLLER
    # -----------------------------------------------------------------

    # -----------------------------------------------------------------
    # SCHEDULE — QUÉT & NHẬP THEO LỊCH DẠY (CDP SCAN + PYAUTOGUI FILL)
    # -----------------------------------------------------------------

    def _launch_schedule_worker(self, params, resume=False):
        """Khởi chạy worker schedule với state UI phù hợp cho run mới hoặc resume."""
        planned_total = max(int(params.get("planned_total", 1) or 1), 1)
        resume_state = params.get("resume_state") or {}
        schedule_mode = str(
            params.get("schedule_mode", SCHEDULE_MODE_MANUAL) or SCHEDULE_MODE_MANUAL
        )
        base_params = copy.deepcopy({k: v for k, v in params.items() if k != "resume_state"})
        processed_count = (
            int(resume_state.get("completed", 0) or 0)
            + int(resume_state.get("skipped", 0) or 0)
            + int(resume_state.get("errors", 0) or 0)
        )
        next_ppct = resume_state.get("next_ppct", params.get("ppct_start"))
        last_success_ppct = resume_state.get("last_success_ppct")

        if not resume:
            self._schedule_results = []
            initial_resume_state = self._build_initial_schedule_resume_state(params)
            self._update_schedule_resume_snapshot(
                resume_state=initial_resume_state,
                params=base_params,
            )
            self._schedule_last_summary = None
        elif not self._schedule_resume_params:
            self._update_schedule_resume_snapshot(
                resume_state=copy.deepcopy(resume_state) if resume_state else self._build_initial_schedule_resume_state(params),
                params=copy.deepcopy({k: v for k, v in params.items() if k != "resume_state"}),
            )
        else:
            self._update_schedule_resume_snapshot(
                resume_state=copy.deepcopy(resume_state) if resume_state else self._schedule_resume_state,
                params=base_params,
            )

        if self._schedule_summary_window and self._schedule_summary_window.winfo_exists():
            try:
                self._schedule_summary_window.destroy()
            except Exception:
                pass

        self._schedule_running = True
        self._schedule_stop_event.clear()
        self._schedule_stop_reason = None
        self._set_schedule_button_states(running=True, can_resume=False)
        self._set_auto_login_button_state()
        self.sched_progressbar["maximum"] = planned_total
        self.sched_progressbar["value"] = min(processed_count, planned_total)
        self._set_sched_live_progress(
            current=processed_count,
            total=planned_total,
            phase="Schedule",
            detail="Đang khởi chạy worker và đồng bộ checkpoint",
            state="running",
        )

        tuan_count = int(params["tuan_to"]) - int(params["tuan_from"]) + 1
        mode_text = "tiếp tục" if resume else "bắt đầu"
        if schedule_mode == SCHEDULE_MODE_KHDH:
            lop_list = list(params.get("lop_list") or [params.get("lop")])
            lop_desc = (
                str(lop_list[0])
                if len(lop_list) <= 1
                else f"{len(lop_list)} lớp ({', '.join(str(x) for x in lop_list[:3])}"
                + (f"... +{len(lop_list) - 3}" if len(lop_list) > 3 else "")
                + ")"
            )
            self.lbl_sched_progress.config(
                text=f"🚀 Schedule {mode_text}: {tuan_count} tuần — {lop_desc} — mode KHDH"
            )
        else:
            self.lbl_sched_progress.config(
                text=(
                    f"🚀 Schedule {mode_text}: {tuan_count} tuần × "
                    f"{len(params['slots'])} tiết/tuần — Lớp {params['lop']}"
                )
            )
        self._update_sched_ppct_runtime(
            last_success=last_success_ppct,
            next_ppct=next_ppct,
            status="running",
        )

        worker_target = self._schedule_worker_khdh if schedule_mode == SCHEDULE_MODE_KHDH else self._schedule_worker
        self._schedule_thread = threading.Thread(
            target=worker_target, args=(params,), daemon=True
        )
        self._schedule_thread.start()
        self.root.after(100, self._poll_schedule_queue)

    def _on_schedule_run(self):
        """Bắt đầu quét & nhập theo lịch dạy từ đầu."""
        if not self._cdp_connected:
            self._log("Chưa kết nối CDP!", "error")
            return
        if self._delete_running or self._delete_scanning:
            messagebox.showwarning(
                "Cảnh báo",
                "Đang có thao tác Xóa dữ liệu chạy. Hãy chờ xong rồi mới chạy schedule.",
            )
            return
        if self._class_stats_thread and self._class_stats_thread.is_alive():
            messagebox.showwarning(
                "Cảnh báo",
                "Đang có phiên thống kê lớp sử dụng CDP. Hãy chờ xong rồi mới chạy schedule.",
            )
            return

        if self._schedule_running:
            self._log("Schedule đang chạy!", "warning")
            return

        schedule_mode = self._get_sched_mode()
        lop_list = self._get_sched_lop_list(schedule_mode)
        sched_lop = lop_list[0] if lop_list else ""
        if not sched_lop:
            self._log("Chưa chọn Lớp!", "warning")
            messagebox.showwarning("Cảnh báo", "Chưa chọn Lớp trong panel Lịch dạy.")
            return

        try:
            tuan_from = self.var_sched_tuan_from.get()
            tuan_to = self.var_sched_tuan_to.get()
        except (tk.TclError, ValueError):
            self._log("Tuần không hợp lệ!", "error")
            return
        if tuan_from > tuan_to:
            tuan_from, tuan_to = tuan_to, tuan_from

        hs_nghi = self.var_sched_hs_nghi.get() or "0"
        diem = self.var_sched_diem.get() or "10"
        nhan_xet_raw = self.var_sched_nhan_xet.get() or "Lớp học chăm ngoan"
        tuan_count = tuan_to - tuan_from + 1
        params = {
            "port": self._cdp_port,
            "tuan_from": tuan_from,
            "tuan_to": tuan_to,
            "lop": sched_lop,
            "lop_list": lop_list,
            "hs_nghi": hs_nghi,
            "diem": diem,
            "nhan_xet_raw": nhan_xet_raw,
            "schedule_mode": schedule_mode,
        }

        if schedule_mode == SCHEDULE_MODE_KHDH:
            khdh_invalid_reason = self._get_sched_mode_invalid_reason(SCHEDULE_MODE_KHDH)
            if khdh_invalid_reason:
                self._show_sched_form_blocked_warning(
                    "Chưa sẵn sàng chạy schedule KHDH",
                    khdh_invalid_reason,
                )
                return
            ok_preflight, preflight_msg = self._preflight_khdh_schedule_context(self._cdp_port)
            if not ok_preflight:
                self._show_sched_form_blocked_warning(
                    "Trang live chưa sẵn sàng cho KHDH",
                    preflight_msg,
                )
                return
            est_total = max(tuan_count * max(len(lop_list), 1), 1)
            lop_desc = sched_lop if len(lop_list) == 1 else f"{len(lop_list)} lớp"
            self.lbl_sched_progress.config(
                text=f"🚀 {tuan_count} tuần — {lop_desc} — mode KHDH"
            )
            self._log(
                f"📅 Schedule KHDH bắt đầu: Tuần {tuan_from}→{tuan_to}, "
                f"{lop_desc} ({', '.join(lop_list)}). "
                "App sẽ quét toàn bộ row chữ đỏ theo KHDH của từng tuần.",
                "info",
            )
            params.update({
                "slots": [],
                "ppct_start": 0,
                "planned_total": est_total,
            })
        else:
            slots = self._get_schedule_slots()
            if not slots:
                self._log("Chưa cấu hình Lịch dạy! Check ít nhất 1 tiết.", "warning")
                messagebox.showwarning(
                    "Cảnh báo",
                    "Chưa cấu hình tiết nào trong Lịch dạy.\n"
                    "Hãy chọn Buổi và check các tiết cần nhập."
                )
                return

            subject_selection, subject_error = self._resolve_current_sched_subject_selection()
            if subject_selection is None:
                self._show_sched_form_blocked_warning(
                    "Chưa sẵn sàng chạy schedule",
                    subject_error,
                )
                return

            try:
                ppct_start = self.var_sched_ppct_start.get()
            except (tk.TclError, ValueError):
                ppct_start = 1

            est_total = tuan_count * len(slots)
            slots_desc = ", ".join(
                f"T{s['thu']}{s['buoi'][0]}T{s['tiet']}" for s in slots[:6]
            )
            if len(slots) > 6:
                slots_desc += f"... (+{len(slots)-6})"
            self.lbl_sched_progress.config(
                text=f"🚀 {tuan_count} tuần × {len(slots)} tiết/tuần — Lớp {sched_lop}"
            )
            self._log(
                f"📅 Schedule bắt đầu: Tuần {tuan_from}→{tuan_to}, "
                f"Lớp {sched_lop}, Slots: {slots_desc}",
                "info"
            )

            params.update({
                "slots": slots,
                "ppct_start": ppct_start,
                "planned_total": est_total,
            })
            params.update(subject_selection)
        self._launch_schedule_worker(params, resume=False)

    def _on_schedule_resume(self):
        """Tiếp tục schedule từ checkpoint gần nhất, không chạy lại từ đầu."""
        if not self._cdp_connected:
            self._log("Chưa kết nối CDP!", "error")
            return
        if self._delete_running or self._delete_scanning:
            messagebox.showwarning(
                "Cảnh báo",
                "Đang có thao tác Xóa dữ liệu chạy. Hãy chờ xong rồi mới tiếp tục schedule.",
            )
            return
        if self._class_stats_thread and self._class_stats_thread.is_alive():
            messagebox.showwarning(
                "Cảnh báo",
                "Đang có phiên thống kê lớp sử dụng CDP. Hãy chờ xong rồi mới tiếp tục schedule.",
            )
            return
        if self._schedule_running:
            self._log("Schedule đang chạy!", "warning")
            return
        if not self._schedule_resume_state or not self._schedule_resume_params:
            messagebox.showinfo("Thông báo", "Không có checkpoint nào để tiếp tục.")
            return

        params = copy.deepcopy(self._schedule_resume_params)
        schedule_mode = str(
            params.get("schedule_mode", SCHEDULE_MODE_MANUAL) or SCHEDULE_MODE_MANUAL
        )
        if schedule_mode != self._get_sched_mode():
            try:
                self.var_sched_mode.set(schedule_mode)
            except Exception:
                pass
            self._apply_sched_mode_state()
        if schedule_mode != SCHEDULE_MODE_KHDH:
            subject_selection, subject_error = self._resolve_current_sched_subject_selection()
            if subject_selection is None:
                self._show_sched_form_blocked_warning(
                    "Chưa sẵn sàng tiếp tục schedule",
                    subject_error,
                )
                return
            params.update(subject_selection)
        else:
            khdh_invalid_reason = self._get_sched_mode_invalid_reason(SCHEDULE_MODE_KHDH)
            if khdh_invalid_reason:
                self._show_sched_form_blocked_warning(
                    "Chưa sẵn sàng tiếp tục schedule KHDH",
                    khdh_invalid_reason,
                )
                return
            ok_preflight, preflight_msg = self._preflight_khdh_schedule_context(self._cdp_port)
            if not ok_preflight:
                self._show_sched_form_blocked_warning(
                    "Trang live chưa sẵn sàng cho KHDH",
                    preflight_msg,
                )
                return
        params["resume_state"] = copy.deepcopy(self._schedule_resume_state)
        next_lop_idx = int(params["resume_state"].get("next_lop_idx", 0) or 0)
        lop_list = list(params.get("lop_list") or [params.get("lop")])
        next_lop_text = (
            str(lop_list[next_lop_idx])
            if 0 <= next_lop_idx < len(lop_list)
            else str(params.get("lop", ""))
        )
        next_ppct = params["resume_state"].get("next_ppct")
        next_tuan = params["resume_state"].get("next_tuan_num")
        next_slot_idx = params["resume_state"].get("next_slot_idx", 0)
        next_row_key = params["resume_state"].get("next_row_key")
        if schedule_mode == SCHEDULE_MODE_KHDH:
            self._log(
                f"↻ Tiếp tục schedule KHDH từ Lớp {next_lop_text}, Tuần {next_tuan}, "
                f"row gợi ý #{next_slot_idx + 1}"
                + (f" | key={next_row_key}" if next_row_key else ""),
                "info",
            )
        else:
            self._log(
                f"↻ Tiếp tục schedule từ Tuần {next_tuan}, slot #{next_slot_idx + 1}, "
                f"PPCT nội bộ {next_ppct}",
                "info",
            )
        self._launch_schedule_worker(params, resume=True)

    def _request_schedule_stop(self, source="nút Dừng"):
        """Yêu cầu dừng schedule tại checkpoint an toàn tiếp theo."""
        if not self._schedule_running:
            return False
        if self._schedule_stop_event.is_set():
            return True

        self._schedule_stop_reason = source
        self._schedule_stop_event.set()
        self.btn_sched_stop.config(state="disabled")
        self.lbl_sched_progress.config(text="⏸ Đang dừng an toàn sau slot hiện tại...")
        self._set_sched_live_progress(
            current=None,
            total=None,
            phase="Schedule",
            detail="Đang dừng an toàn sau slot hiện tại",
            state="paused",
        )
        self._log(f"⏹ Đang dừng schedule ({source})...", "warning")
        return True

    def _on_schedule_stop(self):
        """Dừng schedule qua nút bấm."""
        self._request_schedule_stop("nút Dừng")

    def _on_hotkey_escape(self, _event=None):
        """Hotkey Esc: dừng schedule ngay tại checkpoint an toàn gần nhất."""
        if not self._schedule_running:
            return None
        self._request_schedule_stop("phím Esc")
        return "break"

"""Chạy khôi phục và báo cáo."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox
from typing import TYPE_CHECKING

from ..dialogs.import_tkb import _format_week_runs
from ..theme import DEFAULT_CDP_PORT
from ..workers.restore import (
    RESTORE_MODE_MERGE,
    RESTORE_MODE_OVERWRITE,
    RESTORE_MODE_SKIP,
    RestoreWorker,
)

if TYPE_CHECKING:  # chỉ dùng cho chú thích kiểu
    from ..workers.restore import RestoreReport


class RestoreActionsMixin:
    """Chạy khôi phục và báo cáo."""

    # -----------------------------------------------------------
    # Restore actions
    # -----------------------------------------------------------

    def _on_restore_clicked(self):
        if self._restore_worker is not None and self._restore_worker.is_alive():
            return
        if self._loaded_backup is None:
            messagebox.showerror(
                "Chưa có tệp",
                "Hãy chọn tệp sao lưu trước.",
                parent=self,
            )
            return
        if self.wizard._guard_cdp_exclusive("Khôi phục KHDH"):
            return
        # Lấy danh sách tuần được check
        target = sorted([
            t for t, var in self._week_check_vars.items() if var.get()
        ])
        if not target:
            messagebox.showinfo(
                "Chưa chọn tuần",
                "Hãy đánh dấu ít nhất 1 tuần để khôi phục.",
                parent=self,
            )
            return
        # Loại tuần không có dữ liệu trong tệp
        valid_target = []
        skipped_missing = []
        for t in target:
            wk = self._loaded_backup.week_by_num(t)
            if wk is None or not wk.fetched_at or len(wk.slots) == 0:
                skipped_missing.append(t)
            else:
                valid_target.append(t)
        if not valid_target:
            messagebox.showerror(
                "Không tuần nào hợp lệ",
                "Tất cả tuần đã chọn đều không có dữ liệu trong tệp sao lưu.",
                parent=self,
            )
            return
        if skipped_missing:
            ans = messagebox.askyesno(
                "Có tuần thiếu dữ liệu",
                f"{len(skipped_missing)} tuần ({skipped_missing[:8]}…) "
                "không có dữ liệu trong tệp → sẽ bỏ qua.\n\n"
                f"Tiếp tục với {len(valid_target)} tuần còn lại?",
                parent=self,
            )
            if not ans:
                return

        # Confirm cuối cùng
        mode_lbl = {
            RESTORE_MODE_OVERWRITE: "Xóa trước, ghi lại toàn bộ",
            RESTORE_MODE_MERGE: "Xóa trước, ghi lại từ tệp sao lưu",
            RESTORE_MODE_SKIP: "Xóa trước, ghi lại tuần đã chọn",
        }.get(self.var_restore_conflict.get(), "?")
        snap_lbl = "BẬT" if self.var_auto_snapshot.get() else "TẮT"
        fast_lbl = "BẬT" if self.var_restore_fast_safe.get() else "TẮT"
        weeks_lbl = ", ".join(f"T{t}" for t in valid_target[:14])
        if len(valid_target) > 14:
            weeks_lbl += f", ... (+{len(valid_target) - 14} tuần)"
        if not messagebox.askyesno(
            "Xác nhận khôi phục",
            f"Khôi phục {len(valid_target)} tuần từ tệp:\n"
            f"  {Path(self.var_restore_path.get()).name}\n\n"
            f"Tuần sẽ xử lý: {weeks_lbl}\n\n"
            "Bước bắt buộc trước khi ghi: XÓA các tuần đã chọn trên VnEdu, "
            "sau đó mới ghi lại từ tệp sao lưu để tránh trùng dữ liệu cuốn chiếu.\n\n"
            f"Chế độ: {mode_lbl}\n"
            f"Tự sao lưu nhanh: {snap_lbl}\n\n"
            f"Chế độ nhanh an toàn: {fast_lbl}\n\n"
            "Công cụ sẽ DỪNG NGAY nếu 1 tuần lỗi (giữ an toàn).\n\n"
            "Tiếp tục?",
            parent=self,
        ):
            return

        # Spawn worker
        self._restore_stop = threading.Event()
        self._restore_queue = queue.Queue()
        try:
            port = int(self.wizard.var_port.get())
        except (tk.TclError, ValueError):
            port = DEFAULT_CDP_PORT
        self._restore_worker = RestoreWorker(
            port=port,
            backup=self._loaded_backup,
            target_weeks=valid_target,
            conflict_mode=self.var_restore_conflict.get(),
            excel_path=self.wizard._profile_path,
            auto_snapshot=bool(self.var_auto_snapshot.get()),
            fast_safe_mode=bool(self.var_restore_fast_safe.get()),
            event_queue=self._restore_queue,
            stop_event=self._restore_stop,
        )
        self._restore_worker.start()
        self.btn_restore_run.configure(state="disabled")
        self.btn_restore_stop.configure(state="normal")
        self._restore_progress_bar.configure(maximum=len(valid_target))
        self.var_restore_progress.set(0)
        self.var_restore_status.set(
            f"🚀 Đang khôi phục {len(valid_target)} tuần"
            + (" (nhanh an toàn)" if self.var_restore_fast_safe.get() else "")
            + "…"
        )
        self._restore_done_count = 0
        self._poll_after_id = self.after(150, self._poll_restore_queue)

    def _on_restore_stop(self):
        if self._restore_worker and self._restore_worker.is_alive():
            self._restore_stop.set()
            self.btn_restore_stop.configure(state="disabled")
            self.var_restore_status.set("⏸ Đang dừng…")

    def _poll_restore_queue(self):
        try:
            while True:
                ev = self._restore_queue.get_nowait()
                kind = ev[0]
                if kind == "status":
                    self.var_restore_status.set(str(ev[1]))
                elif kind == "event":
                    xev = ev[1]
                    t = getattr(xev, "event_type", "")
                    if t == "week_done":
                        self._restore_done_count += 1
                        self.var_restore_progress.set(self._restore_done_count)
                        self.var_restore_status.set(
                            f"Tuần {xev.tuan}: {xev.message}"
                        )
                    elif t in ("error", "halt"):
                        self.var_restore_status.set(
                            f"❌ Tuần {xev.tuan}: {xev.message[:200]}"
                        )
                    elif t == "save":
                        self.var_restore_status.set(
                            f"Tuần {xev.tuan}: đang lưu…"
                        )
                    elif t == "week_start":
                        self.var_restore_status.set(
                            f"Tuần {xev.tuan}: bắt đầu"
                        )
                    elif t == "blocker_stats":
                        # v3.3: Real-time số XHR autofill đã chặn
                        d = xev.detail or {}
                        cnt = int(d.get("count") or 0)
                        by = d.get("by_transport") or {}
                        self._update_blocker_label(cnt, by)
                elif kind == "done":
                    report = ev[1]
                    self._show_restore_report(report)
                elif kind == "error":
                    self.var_restore_status.set("❌ Lỗi khôi phục")
                    messagebox.showerror(
                        "Lỗi khôi phục",
                        str(ev[1]).splitlines()[0],
                        parent=self,
                    )
        except queue.Empty:
            pass
        if self._restore_worker and self._restore_worker.is_alive():
            self._poll_after_id = self.after(150, self._poll_restore_queue)
        else:
            self._poll_after_id = None
            self.btn_restore_run.configure(state="normal")
            self.btn_restore_stop.configure(state="disabled")

    def _update_blocker_label(self, count: int, by_transport: dict):
        """Cập nhật nhãn số yêu cầu tự điền đã bị chặn.

        Tracking var: `var_blocker_status` (StringVar) — caller `_build_*_tab`
        đảm bảo đã tạo. Nếu chưa có (compat với code cũ), no-op.
        """
        var = getattr(self, "var_blocker_status", None)
        if var is None:
            return
        try:
            xhr = int(by_transport.get("xhr") or 0)
            fetch_n = int(by_transport.get("fetch") or 0)
            script = int(by_transport.get("script") or 0)
            if count <= 0:
                var.set("🛡 Bộ chặn tự điền: 0 yêu cầu")
                return
            parts = []
            if xhr:
                parts.append(f"ô dữ liệu={xhr}")
            if fetch_n:
                parts.append(f"tải ngầm={fetch_n}")
            if script:
                parts.append(f"mã tự chạy={script}")
            detail = " · ".join(parts) if parts else "?"
            var.set(f"🛡 Bộ chặn tự điền: đã chặn {count} yêu cầu ({detail})")
        except Exception:
            pass

    def _show_restore_report(self, report: "RestoreReport"):
        ok = [r for r in report.week_results if r.save_ok and not r.skipped]
        fail = [r for r in report.week_results if not r.save_ok and not r.skipped]
        skip = [r for r in report.week_results if r.skipped]

        # Format compact: gom tuần liên tiếp thành dải "1-5, 7, 10-12"
        ok_list = self._format_weeks_compact([r.tuan for r in ok])
        fail_list = ", ".join(str(r.tuan) for r in fail) if fail else ""
        skip_list = ", ".join(str(r.tuan) for r in skip) if skip else ""

        lines = [
            "📊 KẾT QUẢ KHÔI PHỤC",
            "",
            f"✓ Thành công: {len(ok)} tuần",
        ]
        if ok:
            lines.append(f"   → Tuần: {ok_list}")
        lines.append("")
        lines.append(f"✗ Thất bại: {len(fail)} tuần")
        if fail:
            lines.append(f"   → Tuần: {fail_list}")
            lines.append("")
            lines.append("Chi tiết từng tuần lỗi:")
            for r in fail[:8]:
                msg = (r.save_msg or "(không có thông báo)")[:140]
                lines.append(f"   • Tuần {r.tuan}: {msg}")
            if len(fail) > 8:
                lines.append(f"   • … và {len(fail) - 8} tuần khác")
        lines.append("")
        if skip:
            lines.append(f"⏭ Bỏ qua: {len(skip)} tuần")
            lines.append(f"   → Tuần: {skip_list}")
            lines.append("")
        lines.append(f"⏱ Thời gian: {report.total_duration_ms / 1000:.1f}s")

        if fail:
            lines.append("")
            lines.append("CÁCH XỬ LÝ:")
            lines.append(
                "  1. Mở web kiểm tra trực tiếp các tuần thất bại "
                "(thường có ô vàng cảnh báo)."
            )
            lines.append(
                "  2. Nếu mất tiết hoặc VnEdu ghi không trọn: mở tab "
                "📸 Bản lưu nhanh → khôi phục lại từ bản lưu trước."
            )
            lines.append(
                "  3. Sau khi sửa các ô vàng trên VnEdu, chạy lại khôi phục "
                "từ tuần thất bại đầu tiên."
            )

        summary = "\n".join(lines)
        self.var_restore_status.set(
            f"{'✓' if not fail else '⚠'} Hoàn tất: "
            f"{len(ok)} OK · {len(fail)} lỗi · {len(skip)} bỏ qua"
        )
        if fail:
            messagebox.showwarning("Khôi phục — có lỗi", summary, parent=self)
        elif report.stopped:
            messagebox.showinfo("Đã dừng", summary, parent=self)
        else:
            messagebox.showinfo("Khôi phục thành công", summary, parent=self)
        try:
            self._refresh_snapshots_list()
        except Exception:
            pass

    @staticmethod
    def _format_weeks_compact(weeks: list[int]) -> str:
        """Gom tuần liên tiếp thành dải. [1,2,3,5,6,8] → '1-3, 5-6, 8'."""
        return _format_week_runs(weeks, empty_text="(không có)", dash="-")

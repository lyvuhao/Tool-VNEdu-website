"""Xoá KHDH theo tuần."""

from __future__ import annotations

import queue
import tkinter as tk
from tkinter import messagebox

from ..dialogs.delete_weeks import DeleteWeeksDialog
from ..dialogs.import_tkb import _format_week_runs
from ..theme import DEFAULT_CDP_PORT
from ..workers.delete_weeks import DeleteWeeksWorker


class DeleteWeeksMixin:
    """Xoá KHDH theo tuần."""

    # -----------------------------------------------------------
    # Delete Weeks Worker (xóa tuần KHDH)
    # -----------------------------------------------------------

    def _on_delete_weeks_clicked(self):
        """Mở DeleteWeeksDialog → spawn worker nếu user xác nhận.

        SIẾT LOGIC AN TOÀN (đọc kỹ trước khi sửa):
        1. Block nếu chưa kết nối CDP / có worker khác đang chạy.
        2. Chỉ open dialog — KHÔNG bao giờ trực tiếp gọi delete API.
        3. Default range = tuan_from..tuan_to của profile (giúp user tránh
           gõ nhầm). Nếu profile chưa có → 1..1.
        4. Dialog tự enforce 2 lần confirm (gõ "XÓA" + askyesno).
        5. Worker chạy độc lập, halt-on-error.
        """
        if self._guard_cdp_exclusive("Xóa tuần KHDH"):
            return

        # Default range — ưu tiên profile range, fallback 1..1
        default_from = 1
        default_to = 1
        if self.profile is not None:
            try:
                default_from = max(1, min(52, int(self.profile.tuan_from or 1)))
                default_to = max(default_from, min(52, int(self.profile.tuan_to or default_from)))
            except Exception:
                pass

        # GV name + nam_hoc + cap để dialog hiển thị
        gv_name = ""
        nam_hoc = 0
        cap_text = ""
        if self.ctx_info is not None:
            gv_name = str(getattr(self.ctx_info, "giao_vien_name", "") or "")
            try:
                nam_hoc = int(getattr(self.ctx_info, "nam_hoc", 0) or 0)
            except Exception:
                pass
            cap_text = str(getattr(self.ctx_info, "cap_hoc_text", "") or "")
        if not gv_name and self.profile is not None:
            gv_name = str(getattr(self.profile, "ho_ten_gv", "") or "")
        if not nam_hoc and self.profile is not None:
            try:
                nam_hoc = int(getattr(self.profile, "nam_hoc", 0) or 0)
            except Exception:
                pass

        dlg = DeleteWeeksDialog(
            self.winfo_toplevel(),
            gv_name=gv_name,
            nam_hoc=nam_hoc,
            cap_hoc_text=cap_text,
            default_from=default_from,
            default_to=default_to,
        )
        self.wait_window(dlg)

        if dlg.confirmed_range is None:
            self._log("Đã hủy xóa tuần KHDH.", "info")
            return

        tuan_from, tuan_to = dlg.confirmed_range
        # Re-validate lần cuối — defensive (mặc dù dialog đã validate)
        if not (1 <= tuan_from <= tuan_to <= 52):
            messagebox.showerror(
                "Tuần không hợp lệ",
                f"Khoảng {tuan_from}..{tuan_to} không hợp lệ.",
                parent=self,
            )
            return

        # Spawn worker
        self._delete_stop_event.clear()
        self._delete_queue = queue.Queue()
        try:
            port = int(self.var_port.get())
        except (tk.TclError, ValueError):
            port = DEFAULT_CDP_PORT
        try:
            self._delete_worker = DeleteWeeksWorker(
                port=port,
                tuan_from=tuan_from,
                tuan_to=tuan_to,
                event_queue=self._delete_queue,
                stop_event=self._delete_stop_event,
            )
        except (ValueError, TypeError) as e:
            messagebox.showerror(
                "Lỗi tham số",
                f"Không khởi tạo được worker:\n{e}",
                parent=self,
            )
            return

        self._delete_worker.start()
        # Lock UI một phần — disable nút xóa, vẫn cho phép Esc dừng
        self.btn_delete_weeks.configure(state="disabled")
        count = tuan_to - tuan_from + 1
        self.progress_bar.configure(maximum=count)
        self.var_progress.set(0)
        self.var_progress_text.set(f"0 / {count}")
        self.var_status.set(
            f"Đang xóa {count} tuần ({tuan_from}→{tuan_to})…"
        )
        self._log(
            f"━━ XÓA TUẦN KHDH: tuần {tuan_from} → {tuan_to} "
            f"({count} tuần) — Esc để dừng ━━",
            "warn",
        )
        self._poll_delete_queue()

    def _poll_delete_queue(self):
        try:
            while True:
                ev = self._delete_queue.get_nowait()
                try:
                    self._handle_delete_event(ev)
                except Exception as e:
                    try:
                        self._log(
                            f"⚠ Lỗi xử lý delete event: "
                            f"{type(e).__name__}: {e}",
                            "err",
                        )
                    except Exception:
                        pass
        except queue.Empty:
            pass

        if self._delete_worker and self._delete_worker.is_alive():
            self._poll_after_id = self._safe_after(120, self._poll_delete_queue)
        else:
            self._poll_after_id = None
            # Restore UI
            try:
                self.btn_delete_weeks.configure(state="normal")
            except Exception:
                pass

    def _handle_delete_event(self, ev: tuple):
        kind = ev[0]
        if kind == "status":
            self.var_status.set(ev[1])
            self._log(ev[1], "info")
        elif kind == "progress":
            # Format: (kind, idx, total, tuan, success, msg)
            _, idx, total, tuan, success, msg = ev
            try:
                self.var_progress.set(idx)
                self.var_progress_text.set(f"{idx} / {total}")
            except Exception:
                pass
            if success:
                self._log(f"  ✓ Đã xóa Tuần {tuan} ({idx}/{total})", "ok")
            else:
                # Server từ chối — log rõ message
                self._log(
                    f"  ✗ Tuần {tuan} ({idx}/{total}): {msg or 'thất bại'}",
                    "err",
                )
        elif kind == "done":
            report = ev[1]
            deleted = list(report.get("deleted") or [])
            failed = list(report.get("failed") or [])
            stopped = bool(report.get("stopped"))
            ok = bool(report.get("ok"))

            # Build summary message
            lines = []
            if deleted:
                lines.append(
                    f"✓ Đã xóa {len(deleted)} tuần: "
                    + self._format_weeks_human(deleted)
                )
            if stopped:
                lines.append(
                    f"⏸ Đã dừng theo yêu cầu (sau khi xóa "
                    f"{len(deleted)} tuần)."
                )
            if failed:
                tuan_fail, msg_fail = failed[0]
                lines.append(
                    f"✗ Tuần {tuan_fail} thất bại: {msg_fail}"
                )
                if deleted:
                    last_ok = deleted[-1] if deleted else None
                    if last_ok is not None:
                        lines.append(
                            f"⚠ Các tuần SAU tuần {tuan_fail} CHƯA được xóa "
                            "(do halt-on-error)."
                        )

            summary = "\n".join(lines) if lines else "Không có gì để xóa."
            self.var_status.set(
                f"{'✓ Hoàn tất' if ok else '⚠ Có lỗi'}: "
                f"{len(deleted)} đã xóa, {len(failed)} lỗi"
            )
            self._log(f"━━ Kết thúc xóa tuần ━━\n{summary}",
                      "ok" if ok else "warn")

            if ok:
                messagebox.showinfo(
                    "Xóa tuần thành công",
                    summary, parent=self,
                )
            elif failed:
                messagebox.showwarning(
                    "Xóa tuần — có lỗi",
                    summary
                    + "\n\nVui lòng kiểm tra lý do thất bại trên Nhật ký.",
                    parent=self,
                )
            elif stopped:
                messagebox.showinfo(
                    "Đã dừng",
                    summary, parent=self,
                )
        elif kind == "error":
            self.var_status.set("❌ Lỗi xóa tuần")
            self._log(ev[1], "err")
            messagebox.showerror(
                "Lỗi xóa tuần", ev[1].splitlines()[0], parent=self,
            )

    @staticmethod
    def _format_weeks_human(weeks: list[int]) -> str:
        """Format danh sách tuần dạng human-readable: '1, 2, 3–5, 8'."""
        return _format_week_runs(weeks, empty_text="(rỗng)", dash="–")

"""Lưu/mở hồ sơ, resume, file gần đây."""

from __future__ import annotations

import queue
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

from ...engine.profile.excel_io import load_profile_xlsx, save_profile_xlsx
from ...engine.profile.json_io import load_profile_auto_json, save_profile_auto_json
from ...log import logger
from ..dialogs.confirm_run import ConfirmRunDialog
from ..workers.executor import ExecutorWorker


class ProfileMixin:
    """Lưu/mở hồ sơ, resume, file gần đây."""

    # -----------------------------------------------------------
    # Save / Open profile
    # -----------------------------------------------------------

    def _on_save_profile(self):
        """Lưu hồ sơ vào path hiện tại — overwrite nếu đã có path,
        bật save-as dialog nếu chưa có path."""
        self._save_profile_internal(force_save_as=False)

    def _on_save_profile_as(self):
        """Lưu hồ sơ ra file MỚI — luôn bật save-as dialog.

        Sau khi save-as thành công, `_profile_path` được cập nhật sang
        file mới → các thao tác Lưu (Ctrl+S) và workers (refresh fb,
        delete weeks) tiếp theo sẽ làm việc với file mới này.
        """
        self._save_profile_internal(force_save_as=True)

    def _save_profile_internal(self, force_save_as: bool):
        """Internal helper — caller passes force_save_as=True để bật dialog."""
        from tkinter import filedialog
        # Block khi executor đang chạy — tránh race ghi .auto.json
        # (worker checkpoint callback và main thread cùng ghi 1 file).
        if self._block_if_executor_running(
            "lưu hồ sơ mới" if force_save_as else "lưu hồ sơ"
        ):
            return
        if not self.profile or not any(t.slots for t in self.profile.all_templates()):
            messagebox.showwarning("Trống", "Chưa có lịch dạy để lưu.",
                                 parent=self)
            return

        # Validate profile cơ bản
        try:
            self.profile.sync_ppct_starts_with_templates()
        except Exception as e:
            messagebox.showerror("Lỗi", f"Hồ sơ không hợp lệ: {e}",
                               parent=self)
            return

        # Đường dẫn mặc định
        default_name = f"KHDH_{self.profile.ho_ten_gv}_{self.profile.nam_hoc}.xlsx"
        default_name = default_name.replace(" ", "_")
        # Khi save-as, ưu tiên dùng dir của file đang mở (nếu có) để user
        # không phải navigate lại — initialfile vẫn dùng default_name để
        # khuyến khích user đổi tên (đỡ vô tình overwrite chính file đang mở).
        initial_dir = ""
        if self._profile_path:
            try:
                initial_dir = str(self._profile_path.parent)
            except Exception:
                initial_dir = ""
        if force_save_as or not self._profile_path:
            kw = {
                "title": (
                    "Lưu hồ sơ KHDH (file mới)" if force_save_as
                    else "Lưu hồ sơ KHDH"
                ),
                "defaultextension": ".xlsx",
                "filetypes": [("Excel", "*.xlsx")],
                "initialfile": default_name,
                "parent": self,
            }
            if initial_dir:
                kw["initialdir"] = initial_dir
            chosen = filedialog.asksaveasfilename(**kw)
            if not chosen:
                return
            path = Path(chosen)
            # Defensive: warn nếu user pick lại đúng file đang mở khi
            # bấm "Lưu mới" — đó nhiều khả năng là nhầm.
            if (
                force_save_as
                and self._profile_path
                and path.resolve() == self._profile_path.resolve()
            ):
                if not messagebox.askyesno(
                    "Đè lên file đang mở?",
                    (
                        "Bạn đang chọn lưu mới vào CHÍNH file đang mở:\n\n"
                        f"  {path.name}\n\n"
                        "Như vậy hành vi giống hệt nút [💾 Lưu hồ sơ]. "
                        "Tiếp tục?"
                    ),
                    parent=self,
                ):
                    return
        else:
            path = self._profile_path

        # Update tuần range from UI
        try:
            self.profile.tuan_from = int(self.var_tuan_from.get())
            self.profile.tuan_to = int(self.var_tuan_to.get())
        except (tk.TclError, ValueError):
            pass

        try:
            save_profile_xlsx(self.profile, path)
            save_profile_auto_json(self.profile, path)
        except Exception as e:
            messagebox.showerror("Lỗi lưu", str(e), parent=self)
            return

        self._profile_path = path
        self._is_dirty = False
        self._add_to_recent(path)
        self._refresh_profile_label()
        action_label = "Lưu mới" if force_save_as else "Lưu"
        self.var_status.set(f"✓ Đã {action_label.lower()}: {path.name}")
        self._log(f"{action_label} hồ sơ: {path}", "ok")

    def _on_open_profile(self):
        from tkinter import filedialog
        # Block toàn bộ CDP workers, không chỉ executor — nếu cho swap
        # profile khi RefreshFallbackWorker / DeleteWeeksWorker đang chạy,
        # worker sẽ tiếp tục mutate `_profile_path` cũ trong khi UI đã
        # chuyển sang profile mới → log fallback bị ghi vào sai file.
        if self._block_if_any_cdp_worker_busy("mở hồ sơ khác"):
            return
        if self._is_dirty:
            if not messagebox.askyesno(
                "Bỏ thay đổi?",
                "Bạn có thay đổi chưa lưu. Tiếp tục mở hồ sơ khác?",
                parent=self,
            ):
                return
        path = filedialog.askopenfilename(
            title="Mở hồ sơ KHDH",
            filetypes=[("Excel", "*.xlsx"), ("All files", "*.*")],
            parent=self,
        )
        if not path:
            return
        self._load_profile_from_path(Path(path))

    def _load_profile_from_path(self, path: Path):
        try:
            profile = load_profile_xlsx(path)
        except Exception as e:
            messagebox.showerror("Lỗi mở hồ sơ", str(e), parent=self)
            return

        # Suppress dirty trace trong khi set vars (vars có trace_add → _mark_dirty)
        self._loading_profile = True
        try:
            self.profile = profile
            self._profile_path = path
            self.var_tach_le_chan.set(profile.tach_le_chan)
            self.var_active_tab.set("le" if profile.tach_le_chan else "chinh")
            self.var_tuan_from.set(profile.tuan_from)
            self.var_tuan_to.set(profile.tuan_to)
            if profile.tach_le_chan:
                self._build_le_chan_tabs()
            else:
                self._destroy_le_chan_tabs()
        finally:
            self._loading_profile = False
        # Reset dirty SAU khi đã set xong vars
        self._is_dirty = False

        self._refresh_grid()
        self._refresh_ppct_table()
        self._refresh_tuan_hint()
        self._refresh_holiday_summary()
        self._add_to_recent(path)
        self.var_summary.set(
            f"●  Đã mở hồ sơ: {path.name}. "
            f"GV: {profile.ho_ten_gv}, năm {profile.nam_hoc}."
        )
        self._log(f"Mở hồ sơ: {path}", "ok")

        # Cập nhật trạng thái nút Refresh fallback theo log mới load
        self._refresh_fallback_button_state()
        self._refresh_profile_label()

        # Check resume checkpoint
        self._check_resume_checkpoint()

    def _refresh_profile_label(self):
        """Cập nhật `var_profile_label` theo `_profile_path` hiện tại.

        Format: "📁 <tên-file>" hoặc "📁 (chưa lưu)" khi chưa có file.
        Khi có dirty (`_is_dirty=True`), thêm dấu `*` ở cuối để user biết
        có thay đổi chưa lưu.
        """
        try:
            if self._profile_path:
                name = self._profile_path.name
                marker = " *" if self._is_dirty else ""
                self.var_profile_label.set(f"📁 {name}{marker}")
            else:
                self.var_profile_label.set("📁 (chưa lưu)")
        except Exception as e:
            logger.warning(f"_refresh_profile_label: {e}")

    # -----------------------------------------------------------
    # Resume — Wave 7
    # -----------------------------------------------------------

    def _check_resume_checkpoint(self):
        """Đọc .auto.json — nếu có checkpoint dở dang, hiện banner offer resume."""
        if not self._profile_path:
            return
        try:
            data = load_profile_auto_json(self._profile_path)
        except Exception:
            return
        cp = data.get("checkpoint") if data else None
        if not cp or not isinstance(cp, dict):
            return
        last_done = self._safe_int(cp.get("last_completed_tuan"), default=0)
        tuan_to = self._safe_int(cp.get("tuan_to"), default=0)
        if last_done <= 0 or tuan_to <= 0 or last_done >= tuan_to:
            # Hoàn tất rồi hoặc data invalid — clear stale checkpoint
            try:
                save_profile_auto_json(self.profile, self._profile_path,
                                      checkpoint=None)
            except Exception as e:
                # W2 fix: log thay vì silent — user cần biết tại sao
                # phantom resume prompt vẫn xuất hiện ở lần chạy sau.
                self._log(
                    f"⚠ Không xóa được checkpoint cũ: {e}",
                    "warn",
                )
            return

        next_tuan = last_done + 1
        msg = (
            f"Lần chạy trước dừng ở tuần {last_done}.\n\n"
            f"Bạn có muốn tiếp tục từ tuần {next_tuan} đến {tuan_to}?"
        )
        if messagebox.askyesno(
            "Tiếp tục lần chạy trước?", msg, parent=self,
        ):
            self._resume_from_checkpoint(cp)
        else:
            # User chọn bỏ → clear checkpoint
            try:
                save_profile_auto_json(self.profile, self._profile_path,
                                      checkpoint=None)
                self._log("Đã bỏ qua checkpoint cũ.", "info")
            except Exception as e:
                # W2 fix: log thay vì silent
                self._log(
                    f"⚠ Không xóa được checkpoint cũ: {e}",
                    "warn",
                )

    def _resume_from_checkpoint(self, cp: dict):
        """Spawn ExecutorWorker với resume_from = last_completed_tuan + 1."""
        last_done = self._safe_int(cp.get("last_completed_tuan"), default=0)
        tuan_to = self._safe_int(cp.get("tuan_to"), default=self.profile.tuan_to)
        tuan_from = self._safe_int(cp.get("tuan_from"), default=last_done + 1)
        next_tuan = last_done + 1

        # Set UI tuần range để khớp
        self.profile.tuan_from = tuan_from
        self.profile.tuan_to = tuan_to
        self.var_tuan_from.set(tuan_from)
        self.var_tuan_to.set(tuan_to)

        # Validate profile trước khi resume
        try:
            self.profile.sync_ppct_starts_with_templates()
        except Exception as e:
            messagebox.showerror("Lỗi", f"Hồ sơ không hợp lệ: {e}", parent=self)
            return

        if not self.profile.ppct_starts:
            messagebox.showerror(
                "Lỗi",
                "Không có nhóm PPCT nào. Hồ sơ có thể bị hỏng.",
                parent=self,
            )
            return

        # Guard CDP exclusive
        if self._guard_cdp_exclusive("Tiếp tục nhập"):
            return

        # Confirm dialog
        dlg = ConfirmRunDialog(
            self,
            summary_lines=[
                f"Tiếp tục từ tuần {next_tuan} → {tuan_to}",
                f"Đã hoàn thành: tuần {tuan_from} → {last_done}",
                f"Còn lại: {tuan_to - last_done} tuần",
            ],
            warnings=[
                ("info", "Hồ sơ sẽ áp dụng tiếp với cùng PPCT cuốn chiếu."),
            ],
        )
        self.wait_window(dlg)
        if dlg.choice is None:
            self._log("Hủy resume.", "info")
            return

        dry_run = (dlg.choice == "dry_run")

        # Spawn executor — với resume_from
        self._exec_stop_event.clear()
        self._exec_queue = queue.Queue()
        self._exec_worker = ExecutorWorker(
            port=int(self.var_port.get()),
            profile=self.profile,
            tuan_from=tuan_from, tuan_to=tuan_to,
            dry_run=dry_run,
            excel_path=self._profile_path,
            event_queue=self._exec_queue,
            stop_event=self._exec_stop_event,
            resume_from=next_tuan,
        )
        self._exec_worker.start()
        # v2: Show overlay nếu user bật toggle
        self._overlay_total_weeks = tuan_to - next_tuan + 1
        self._overlay_show_if_enabled()
        self.btn_run.configure(state="disabled")
        self.btn_preview.configure(state="disabled")
        self.btn_refresh_fallback.configure(state="disabled")
        self.btn_fill_missing_titles.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        n_remaining = tuan_to - next_tuan + 1
        self.progress_bar.configure(maximum=n_remaining)
        self.var_progress.set(0)
        self.var_progress_text.set(f"0 / {n_remaining}")
        self.var_status.set(f"Đang tiếp tục từ tuần {next_tuan}…")
        self._log(
            f"━━ TIẾP TỤC: Tuần {next_tuan} → {tuan_to} "
            f"({'CHẠY THỬ' if dry_run else 'ÁP DỤNG THẬT'}) ━━",
            "warn" if not dry_run else "info",
        )
        self._exec_completed_count = 0
        self._poll_exec_queue()

    # -----------------------------------------------------------
    # Recent files
    # -----------------------------------------------------------

    def _load_recent_files(self) -> list[str]:
        try:
            import json
            data = json.loads(self.RECENT_FILES_PATH.read_text(encoding="utf-8"))
            files = data.get("files") or []
            # Filter: chỉ giữ file còn tồn tại
            return [f for f in files if Path(f).exists()][: self.MAX_RECENT_FILES]
        except (FileNotFoundError, OSError, ValueError):
            return []

    def _save_recent_files(self, files: list[str]):
        try:
            import json
            self.RECENT_FILES_PATH.write_text(
                json.dumps({"files": files}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _add_to_recent(self, path: Path):
        files = self._load_recent_files()
        spath = str(path)
        if spath in files:
            files.remove(spath)
        files.insert(0, spath)
        files = files[: self.MAX_RECENT_FILES]
        self._save_recent_files(files)
        self._refresh_recent_menu()

    def _refresh_recent_menu(self):
        self._recent_menu.delete(0, "end")
        files = self._load_recent_files()
        if not files:
            self._recent_menu.add_command(label="(trống)", state="disabled")
            return
        for f in files:
            name = Path(f).name
            self._recent_menu.add_command(
                label=name,
                command=lambda p=Path(f): self._open_recent_profile(p),
            )

    def _open_recent_profile(self, path: Path):
        """Mở profile từ recent menu — guard chung cho mọi CDP worker."""
        if self._block_if_any_cdp_worker_busy("mở hồ sơ từ Recent"):
            return
        if self._is_dirty:
            if not messagebox.askyesno(
                "Bỏ thay đổi?",
                "Bạn có thay đổi chưa lưu. Tiếp tục mở hồ sơ khác?",
                parent=self,
            ):
                return
        self._load_profile_from_path(path)

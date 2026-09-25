"""Chạy / dừng điền KHDH."""

from __future__ import annotations

import queue
import tkinter as tk
from datetime import datetime
from tkinter import messagebox

from playwright.sync_api import sync_playwright

from ...engine.backup.models import BackupFile, BackupMetadata, BackupWeek
from ...engine.backup.snapshots import SnapshotManager
from ...engine.client.client import KHDHClient
from ...engine.profile.profile import count_profile_holiday_rules, validate_profile_holiday_rules
from ...log import logger
from ..dialogs.confirm_run import ConfirmRunDialog
from ..dialogs.progress_overlay import ProgressOverlayWindow
from ..workers.bootstrap import BootstrapWorker
from ..workers.executor import ExecutorWorker


class RunMixin:
    """Chạy / dừng điền KHDH."""

    # -----------------------------------------------------------
    # Run / Stop
    # -----------------------------------------------------------

    def _on_run_clicked(self, dry_run_default: bool):
        # Validate
        if not self.bootstrap_data:
            messagebox.showwarning(
                "Chưa đăng nhập VnEdu",
                "Bạn cần bấm [Đăng nhập VnEdu] trước để tool có dữ liệu áp dụng.",
                parent=self,
            )
            return

        if not self.profile:
            return

        # Sync state
        try:
            self.profile.tuan_from = int(self.var_tuan_from.get())
            self.profile.tuan_to = int(self.var_tuan_to.get())
        except (tk.TclError, ValueError):
            messagebox.showerror("Lỗi", "Tuần từ/đến không hợp lệ.",
                               parent=self)
            return

        # Range check 1..52
        if not (1 <= self.profile.tuan_from <= 52):
            messagebox.showerror(
                "Lỗi",
                f"'Từ tuần' = {self.profile.tuan_from} nằm ngoài khoảng 1–52.",
                parent=self,
            )
            return
        if not (1 <= self.profile.tuan_to <= 52):
            messagebox.showerror(
                "Lỗi",
                f"'Đến tuần' = {self.profile.tuan_to} nằm ngoài khoảng 1–52.",
                parent=self,
            )
            return

        if self.profile.tuan_from > self.profile.tuan_to:
            messagebox.showerror(
                "Lỗi",
                "'Từ tuần' phải nhỏ hơn hoặc bằng 'Đến tuần'.",
                parent=self,
            )
            return

        # Check templates not empty
        templates = self.profile.all_templates()
        if not any(t.slots for t in templates):
            messagebox.showwarning(
                "Lịch trống",
                "Bạn chưa soạn tiết nào. Hãy double-click vào ô lưới để thêm tiết trước.",
                parent=self,
            )
            return

        # Build summary + warnings
        try:
            self.profile.sync_ppct_starts_with_templates()
        except Exception as e:
            messagebox.showerror("Lỗi", str(e), parent=self)
            return

        # Check ppct_starts non-empty (sau sync)
        if not self.profile.ppct_starts:
            messagebox.showerror(
                "Lỗi",
                "Không có nhóm PPCT nào. Hãy soạn ít nhất 1 tiết trong lịch dạy.",
                parent=self,
            )
            return

        # Hard block nếu bất kỳ ppct_start <= 0
        invalid_ppct = [
            e for e in self.profile.ppct_starts if e.ppct_start <= 0
        ]
        if invalid_ppct:
            names = ", ".join(
                f"{e.lop_text}/{e.phan_mon_text}" for e in invalid_ppct[:5]
            )
            messagebox.showerror(
                "PPCT không hợp lệ",
                f"Các nhóm sau có PPCT bắt đầu ≤ 0:\n{names}\n\n"
                f"Hãy double-click cột PPCT trong bảng để sửa (phải ≥ 1).",
                parent=self,
            )
            return

        # Warn nếu bootstrap data cũ (> 30 phút)
        warnings_extra: list[tuple[str, str]] = []
        if self.bootstrap_data and hasattr(self.bootstrap_data, "is_fresh"):
            if not self.bootstrap_data.is_fresh(max_age_hours=0.5):
                warnings_extra.append((
                    "warn",
                    "Dữ liệu lớp/môn đã lấy hơn 30 phút trước. "
                    "Nếu bạn vừa thay đổi phân công trên web, hãy bấm "
                    "[Đăng nhập VnEdu] lại để cập nhật.",
                ))

        summary, warnings = self._build_run_summary(dry_run_default)
        warnings = warnings_extra + warnings

        # Show ConfirmRunDialog
        dlg = ConfirmRunDialog(self, summary_lines=summary, warnings=warnings)
        self.wait_window(dlg)
        if dlg.choice is None:
            return

        dry_run = (dlg.choice == "dry_run")
        self._spawn_executor(dry_run)

    def _build_run_summary(self, dry_run_hint: bool) -> tuple[list[str], list[tuple[str, str]]]:
        """Build summary + warnings list cho ConfirmRunDialog."""
        p = self.profile
        n_tuan = p.tuan_to - p.tuan_from + 1
        if p.tach_le_chan:
            tuan_le = [t for t in range(p.tuan_from, p.tuan_to + 1)
                      if not p.is_chan(t)]
            tuan_chan = [t for t in range(p.tuan_from, p.tuan_to + 1)
                        if p.is_chan(t)]
            n_le = len(tuan_le)
            n_chan = len(tuan_chan)
            slots_le = (p.template_le.slot_count() if p.template_le else 0)
            slots_chan = (p.template_chan.slot_count() if p.template_chan else 0)
            total_slots = n_le * slots_le + n_chan * slots_chan
            summary = [
                "Chế độ: Tuần lẻ và Tuần chẵn (2 mẫu)",
                f"Dải tuần: {p.tuan_from} → {p.tuan_to} ({n_tuan} tuần)",
                f"Tuần lẻ: {n_le} tuần, mỗi tuần {slots_le} tiết",
                f"Tuần chẵn: {n_chan} tuần, mỗi tuần {slots_chan} tiết",
                f"Tổng số tiết dự kiến: {total_slots}",
            ]
        else:
            slots = p.template_chinh.slot_count() if p.template_chinh else 0
            total_slots = n_tuan * slots
            summary = [
                "Chế độ: 1 mẫu cho mọi tuần",
                f"Dải tuần: {p.tuan_from} → {p.tuan_to} ({n_tuan} tuần)",
                f"Mỗi tuần: {slots} tiết",
                f"Tổng số tiết dự kiến: {total_slots}",
            ]

        n_nghi, n_bu = count_profile_holiday_rules(
            p, p.tuan_from, p.tuan_to
        )
        if n_nghi or n_bu:
            summary.append(
                f"Nghỉ/Dạy bù: {n_nghi} tiết Nghỉ, {n_bu} tiết Dạy bù"
            )

        warnings: list[tuple[str, str]] = []

        # Warning 1: 2 template giống nhau khi tach_le_chan
        if p.tach_le_chan and p.templates_equal():
            warnings.append((
                "warn",
                "Tuần lẻ và tuần chẵn đang giống y hệt nhau. "
                "Có thể bạn không cần tách. Bỏ tick checkbox để dùng 1 mẫu cho gọn.",
            ))

        # Warning 2: PPCT có giá trị bất thường
        for entry in p.ppct_starts:
            if entry.ppct_start <= 0:
                warnings.append((
                    "error",
                    f"PPCT bắt đầu của {entry.lop_text} – {entry.phan_mon_text} = "
                    f"{entry.ppct_start}. Phải ≥ 1.",
                ))

        # Warning 3: dải tuần dài
        if n_tuan > 30:
            warnings.append((
                "warn",
                f"Dải tuần dài ({n_tuan} tuần). Sẽ mất khoảng "
                f"{n_tuan * 5 // 60 + 1} phút.",
            ))

        # Warning 3b: quy tắc Nghỉ/Dạy bù sai hoặc chưa đủ
        warnings.extend(validate_profile_holiday_rules(
            p, p.tuan_from, p.tuan_to
        ))

        # Warning 4: slot có lop/mon/pm không khớp với bootstrap
        if self.bootstrap_data:
            invalid = []
            for tpl in p.all_templates():
                for s in tpl.slots:
                    if not self.bootstrap_data.has_phan_mon(
                        s.lop_id, s.mon_id, s.phan_mon_id
                    ):
                        invalid.append(
                            f"{s.thu_text}-{s.buoi_text}-{s.tiet}: "
                            f"{s.lop_text} {s.mon_text}"
                        )
            if invalid:
                preview = "; ".join(invalid[:3])
                if len(invalid) > 3:
                    preview += f" và {len(invalid) - 3} tiết khác"
                warnings.append((
                    "error",
                    f"Có {len(invalid)} tiết có lớp/môn/phân môn không khớp "
                    f"với danh sách web: {preview}",
                ))

        return summary, warnings

    def _do_pre_run_snapshot(self):
        """Quét nhanh range tuần sắp RUN + dump snapshot vào folder.

        Best-effort: nếu fail → log warning + tiếp tục RUN. User đã có
        file Excel hồ sơ, snapshot chỉ là "safety net" trong trường hợp
        RUN sai → revert qua dialog Khôi phục.

        Snapshot này chạy SYNC (không thread) trong main thread vì:
        - Block ngắn (~10s cho 36 tuần) — chấp nhận được trước RUN.
        - Không cần queue/poll → đơn giản hơn.
        - Tránh race với ExecutorWorker (worker chuẩn bị spawn ngay sau).
        """
        if self._profile_path is None:
            return
        try:
            tuan_from = int(self.var_tuan_from.get())
            tuan_to = int(self.var_tuan_to.get())
        except (tk.TclError, ValueError):
            return
        if not (1 <= tuan_from <= tuan_to <= 52):
            return

        self.var_status.set("📸 Đang tạo snapshot trước khi nhập…")
        try:
            self.update_idletasks()
        except Exception:
            pass

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.connect_over_cdp(
                    f"http://localhost:{int(self.var_port.get())}",
                    timeout=5000,
                )
                page = BootstrapWorker._find_vnedu_page(browser)
                if not page:
                    return
                client = KHDHClient(page)
                ctx = client.fetch_context()
                weeks_data = client.fetch_weeks_parallel(
                    list(range(tuan_from, tuan_to + 1)),
                    is_edit=1, batch_size=6,
                )
        except Exception as e:
            logger.warning(f"pre_run_snapshot fetch failed: {e}")
            return

        # Build BackupFile
        metadata = BackupMetadata(
            created_at=datetime.now().isoformat(timespec="seconds"),
            tool_version="auto_khbd_pro",
            nam_hoc=int(getattr(ctx, "nam_hoc", 0) or 0),
            cap_hoc=int(getattr(ctx, "cap_hoc", 0) or 0),
            cap_hoc_text=str(getattr(ctx, "cap_hoc_text", "") or ""),
            giao_vien_id=int(getattr(ctx, "giao_vien_id", 0) or 0),
            giao_vien_name=str(getattr(ctx, "giao_vien_name", "") or ""),
            ma_truong=str(getattr(ctx, "site_id", "") or ""),
            tuan_from=tuan_from,
            tuan_to=tuan_to,
            note=f"Tự động trước khi nhập tuần {tuan_from}–{tuan_to}",
        )
        weeks: list[BackupWeek] = []
        for tuan in range(tuan_from, tuan_to + 1):
            wd = weeks_data.get(tuan)
            if wd is None:
                weeks.append(BackupWeek(tuan=tuan, fetched_at=""))
            else:
                weeks.append(BackupWeek.from_week_data(wd))
        bf = BackupFile(metadata=metadata, weeks=weeks)
        mgr = SnapshotManager(
            self._profile_path,
            gv_id=int(getattr(ctx, "giao_vien_id", 0) or 0),
            nam_hoc=int(getattr(ctx, "nam_hoc", 0) or 0),
        )
        saved = mgr.save_snapshot(bf, reason="before-run")
        if saved:
            self._log(f"📸 Đã lưu snapshot: {saved.name}", "info")

    def _spawn_executor(self, dry_run: bool):
        if self._guard_cdp_exclusive("Bắt đầu nhập"):
            return

        # Auto-snapshot trước khi chạy thật (KHÔNG snapshot cho dry_run vì
        # dry_run không thay đổi web). Best-effort: fail không block flow.
        if not dry_run and self._profile_path is not None:
            try:
                self._do_pre_run_snapshot()
            except Exception as e:
                logger.warning(f"_do_pre_run_snapshot: {e}")

        # v3: BẮT BUỘC bật fallback dấu cách cho mọi RUN — phân môn không
        # có CSDL tên bài (HĐTN, SHL, Chào cờ...) sẽ rỗng → web reject save
        # → mất tiết. Checkbox UI chỉ mang tính thông báo, không cho phép tắt.
        ten_bai_fallback_locked = True

        self._exec_stop_event.clear()
        self._exec_queue = queue.Queue()
        self._exec_worker = ExecutorWorker(
            port=int(self.var_port.get()),
            profile=self.profile,
            tuan_from=self.profile.tuan_from,
            tuan_to=self.profile.tuan_to,
            dry_run=dry_run,
            excel_path=self._profile_path,
            event_queue=self._exec_queue,
            stop_event=self._exec_stop_event,
            ten_bai_fallback=ten_bai_fallback_locked,
        )
        self._exec_worker.start()

        # v2: Tạo & show overlay nếu user bật toggle
        self._overlay_total_weeks = (
            self.profile.tuan_to - self.profile.tuan_from + 1
        )
        self._overlay_show_if_enabled()

        # Lock UI
        self.btn_run.configure(state="disabled")
        self.btn_preview.configure(state="disabled")
        self.btn_refresh_fallback.configure(state="disabled")
        self.btn_fill_missing_titles.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        n_tuan = self.profile.tuan_to - self.profile.tuan_from + 1
        self.progress_bar.configure(maximum=n_tuan)
        self.var_progress.set(0)
        self.var_progress_text.set(f"0 / {n_tuan}")
        self.var_status.set("Đang chuẩn bị…")
        mode = "CHẠY THỬ (không lưu)" if dry_run else "ÁP DỤNG THẬT"
        self._log(f"━━ {mode}: Tuần {self.profile.tuan_from} → {self.profile.tuan_to} ━━",
                "warn" if not dry_run else "info")
        if ten_bai_fallback_locked:
            self._log(
                "🛟 Fallback dấu cách cho ô Tên bài dạy: BẬT — các ô không có "
                "tên bài sẽ được chèn 1 dấu cách để qua bước validate.",
                "warn",
            )
        self._exec_completed_count = 0
        self._poll_exec_queue()

    def _poll_exec_queue(self):
        try:
            while True:
                ev = self._exec_queue.get_nowait()
                try:
                    self._handle_exec_event(ev)
                except Exception as e:
                    # Error boundary: log nhưng KHÔNG crash poll loop
                    try:
                        self._log(
                            f"⚠ Lỗi xử lý event: {type(e).__name__}: {e}",
                            "err",
                        )
                    except Exception:
                        pass
        except queue.Empty:
            pass

        if self._exec_worker and self._exec_worker.is_alive():
            self._poll_after_id = self._safe_after(120, self._poll_exec_queue)
        else:
            self._poll_after_id = None
            self.btn_run.configure(state="normal")
            self.btn_preview.configure(state="normal")
            self.btn_stop.configure(state="disabled")

    def _overlay_show_if_enabled(self):
        """v2: Tạo + show ProgressOverlayWindow nếu user bật toggle.

        Idempotent: nếu overlay đã tồn tại + còn alive → reuse, chỉ show + reset
        counters. Nếu đã bị destroy ngầm (Tk recycled, alt+f4...) → tạo mới.
        Errors swallowed (overlay là tính năng phụ).
        """
        try:
            if not self.var_show_overlay.get():
                # User đã tắt toggle — không tạo overlay
                if self._progress_overlay is not None:
                    try:
                        self._progress_overlay.hide()
                    except Exception:
                        pass
                return
            # B2 fix: check winfo_exists trước khi reuse
            need_create = True
            if self._progress_overlay is not None:
                try:
                    if self._progress_overlay.winfo_exists():
                        need_create = False
                except Exception:
                    need_create = True
            if need_create:
                self._progress_overlay = ProgressOverlayWindow(self.winfo_toplevel())
            else:
                # Reuse — reset color về normal
                self._progress_overlay.reset_color()
                self._progress_overlay.show()
        except Exception:
            self._progress_overlay = None

    def _overlay_handle_event(self, xev):
        """v2: Forward executor event sang ProgressOverlayWindow.

        Idempotent + safe: nếu overlay chưa tồn tại / đã hide / user tắt
        toggle → no-op. Errors swallowed (overlay là tính năng phụ, không
        được crash main flow).
        """
        ov = self._progress_overlay
        if ov is None:
            return
        try:
            t = xev.event_type
            if t == "plan_start":
                ov.show()
                ov.reset_color()
                self._overlay_done_count = 0
                self._overlay_error_count = 0
                self._overlay_extras_count = 0
                self._overlay_skipped_count = 0
                ov.update_status("Đang chuẩn bị…")
                ov.update_stats(done=0)
            elif t == "week_start":
                ov.update_status(f"Tuần {xev.tuan}: bắt đầu")
            elif t == "fill_ops":
                ov.update_status(f"Tuần {xev.tuan}: {xev.message[:40]}")
            elif t == "save":
                ov.update_status(f"Tuần {xev.tuan}: đang lưu…")
            elif t == "pre_action":
                ov.update_status(f"Tuần {xev.tuan}: generate từ TKB")
            elif t == "extra_detected":
                self._overlay_extras_count += len(
                    (xev.detail or {}).get("extras") or []
                )
            elif t == "week_done":
                msg = xev.message or ""
                if "ok=True" in msg and "skipped=True" in msg:
                    self._overlay_skipped_count += 1
                elif "ok=True" in msg:
                    self._overlay_done_count += 1
                elif "ok=False" in msg:
                    self._overlay_error_count += 1
                total = self._overlay_total_weeks or 1
                processed = (self._overlay_done_count
                            + self._overlay_error_count
                            + self._overlay_skipped_count)
                ov.update_progress(processed, total, week_num=xev.tuan)
                ov.update_stats(
                    done=self._overlay_done_count,
                    errors=self._overlay_error_count,
                    extras=self._overlay_extras_count,
                    skipped=self._overlay_skipped_count,
                )
            elif t == "week_already_complete":
                ov.update_status(f"Tuần {xev.tuan}: đã đúng — bỏ qua")
            elif t == "week_partial_supplement":
                ov.update_status(f"Tuần {xev.tuan}: bổ sung ô thiếu")
            elif t == "error":
                ov.set_error_mode()
            elif t == "halt":
                ov.set_error_mode()
                ov.update_status(f"⛔ Dừng ở tuần {xev.tuan}")
            elif t == "stop":
                ov.update_status("Đã dừng theo yêu cầu")
            elif t == "plan_done":
                ov.update_status("✓ Hoàn tất")
                ov.auto_hide_after(5000)
        except Exception:
            # Overlay là tính năng phụ — error không được crash event handler
            pass

    def _handle_exec_event(self, ev: tuple):
        kind = ev[0]
        if kind == "status":
            self.var_status.set(ev[1])
        elif kind == "executor_ready":
            # v2: Worker passed executor reference — store cho _refresh_ppct_table
            self._executor = ev[1]
        elif kind == "event":
            xev = ev[1]
            t = xev.event_type
            # v2: forward sang overlay nếu đang hiện
            self._overlay_handle_event(xev)
            if t == "plan_start":
                self._log(f"━━ Bắt đầu chạy: {xev.message} ━━", "info")
            elif t == "week_start":
                self.var_status.set(f"Tuần {xev.tuan}: bắt đầu…")
                self._log(f"Tuần {xev.tuan}: bắt đầu", "info")
            elif t == "week_done":
                self._exec_completed_count += 1
                self.var_progress.set(self._exec_completed_count)
                self.var_progress_text.set(
                    f"{self._exec_completed_count} / "
                    f"{self.progress_bar['maximum']}"
                )
                # Phân biệt success / fail / skip dựa vào message
                msg = xev.message or ""
                if "ok=True" in msg:
                    self._log(f"Tuần {xev.tuan}: ✓ {msg}", "ok")
                elif "ok=False" in msg:
                    self._log(f"Tuần {xev.tuan}: ✗ {msg}", "err")
                else:
                    self._log(f"Tuần {xev.tuan}: {msg}", "info")
                # v2: refresh PPCT table sau mỗi tuần để cột "PPCT sắp nhập"
                # cập nhật real-time với scan-based last_ppct.
                try:
                    self._refresh_ppct_table()
                except Exception:
                    pass
            elif t == "week_skip":
                self._log(f"Tuần {xev.tuan}: bỏ qua — {xev.message}", "warn")
            elif t == "week_already_complete":
                # v2: tuần đã đầy đủ + đúng → log info để user thấy
                self._log(f"✓ Tuần {xev.tuan}: {xev.message}", "ok")
            elif t == "week_partial_supplement":
                # v2: bổ sung ô thiếu
                self._log(f"⊕ Tuần {xev.tuan}: {xev.message}", "info")
            elif t == "extra_detected":
                # v2: phát hiện tiết bù — log info, không gián đoạn
                self._log(f"📌 Tuần {xev.tuan}: {xev.message}", "warn")
            elif t == "fill_ops":
                self._log(f"  Tuần {xev.tuan}: {xev.message}", "info")
            elif t == "fill_done":
                self._log(f"  Tuần {xev.tuan}: {xev.message}", "info")
            elif t == "save":
                self.var_status.set(f"Tuần {xev.tuan}: đang lưu…")
                self._log(f"  Tuần {xev.tuan}: {xev.message}", "info")
            elif t == "warning":
                self._log(f"⚠ Tuần {xev.tuan}: {xev.message}", "warn")
            elif t == "error":
                self._log(f"❌ Tuần {xev.tuan}: {xev.message}", "err")
            elif t == "pre_action":
                self._log(f"Tuần {xev.tuan}: {xev.message}", "info")
            elif t == "stop":
                self._log("Đã dừng theo yêu cầu", "warn")
            elif t == "halt":
                # Tool tự dừng vì có lỗi nghiêm trọng — báo rõ cho user
                self._halted_during_run = True
                self._log(f"⛔ {xev.message}", "err")
                self.var_status.set(f"⛔ Đã dừng tại tuần {xev.tuan}")
                # Show messagebox với message giàu thông tin từ PlanExecutor
                # (đã include server msg, danh sách ô vàng, hint khắc phục).
                try:
                    messagebox.showerror(
                        f"Tool đã dừng tại tuần {xev.tuan}",
                        xev.message,
                        parent=self,
                    )
                except Exception:
                    pass
            elif t == "plan_done":
                self._log(f"━━ {xev.message} ━━",
                          "ok" if "errors=0" in (xev.message or "") else "warn")
        elif kind == "checkpoint":
            pass  # silent — đã log ở week_done
        elif kind == "done":
            report = ev[1]
            ok_count = len(report.successful_weeks)
            err_count = report.error_count
            fallback_count = int(getattr(report, "ten_bai_fallback_count", 0) or 0)
            # Nếu đã halt giữa chừng (halt event đã show messagebox), không
            # show messagebox kết thúc nữa để tránh chồng dialog
            already_alerted = self._halted_during_run
            self.var_status.set(
                f"{'⛔' if already_alerted else '✓'} Hoàn tất: "
                f"{ok_count} tuần thành công, {err_count} tuần lỗi"
                + (f", {fallback_count} ô fallback" if fallback_count else "")
            )
            self.var_summary.set(
                f"Hoàn tất {ok_count}/{ok_count + err_count} tuần. "
                + (f"Fallback dấu cách: {fallback_count} ô. " if fallback_count else "")
                + f"Thời gian: {report.total_duration_ms / 1000:.1f}s"
            )
            self._log(
                f"━━ Hoàn tất: {ok_count} thành công, {err_count} lỗi"
                + (f", {fallback_count} ô fallback dấu cách" if fallback_count else "")
                + " ━━",
                "ok" if err_count == 0 else "warn",
            )
            self._halted_during_run = False  # reset cờ
            # Refresh nút fallback — RUN có thể vừa thêm/cleanup entry
            self._refresh_fallback_button_state()
            if already_alerted:
                pass  # đã có dialog từ halt event
            elif err_count == 0:
                fallback_note = (
                    f"\n\nFallback dấu cách: {fallback_count} ô — "
                    "các ô này được chèn 1 dấu cách để qua bước validate. "
                    "Hãy mở web và tự bổ sung Tên bài dạy nếu cần."
                ) if fallback_count else ""
                messagebox.showinfo(
                    "Hoàn tất",
                    f"Đã chạy xong.\n\n"
                    f"Số tuần thành công: {ok_count}\n\n"
                    "Tất cả các tuần đã được lưu thành công."
                    + fallback_note,
                    parent=self,
                )
            else:
                fallback_note = (
                    f"\nFallback dấu cách: {fallback_count} ô"
                ) if fallback_count else ""
                messagebox.showwarning(
                    "Hoàn tất (có lỗi)",
                    f"Đã chạy xong.\n\n"
                    f"Số tuần thành công: {ok_count}\n"
                    f"Số tuần lỗi: {err_count}"
                    + fallback_note + "\n\n"
                    "Xem khung Nhật ký để biết chi tiết tuần nào bị chặn "
                    "(thường do trùng tiết với giáo viên khác hoặc phân môn "
                    "chưa có CSDL tên bài).",
                    parent=self,
                )
        elif kind == "error":
            self.var_status.set("❌ Lỗi")
            self._log(ev[1], "err")
            messagebox.showerror("Lỗi", ev[1].splitlines()[0], parent=self)

    def _on_stop_clicked(self):
        if self._exec_worker and self._exec_worker.is_alive():
            self._exec_stop_event.set()
            self._log("Yêu cầu dừng…", "warn")
            self.btn_stop.configure(state="disabled")
        elif self._refresh_fb_worker and self._refresh_fb_worker.is_alive():
            self._refresh_fb_stop_event.set()
            self._log("Yêu cầu dừng cập nhật fallback…", "warn")
            self.btn_stop.configure(state="disabled")
        elif self._fill_titles_worker and self._fill_titles_worker.is_alive():
            self._fill_titles_stop_event.set()
            self._log("Yêu cầu dừng điền tên bài HĐTN…", "warn")
            self.btn_stop.configure(state="disabled")

    def _on_esc(self, _event):
        # Stop executor nếu đang chạy
        if self._exec_worker and self._exec_worker.is_alive():
            self._on_stop_clicked()
            return
        # Stop refresh fallback worker
        if self._refresh_fb_worker and self._refresh_fb_worker.is_alive():
            self._on_stop_clicked()
            return
        if self._fill_titles_worker and self._fill_titles_worker.is_alive():
            self._on_stop_clicked()
            return
        # Stop delete weeks worker — đây là worker NGUY HIỂM, ưu tiên dừng
        # ngay khi user nhấn Esc dù dialog đã đóng (worker vẫn chạy bg).
        if self._delete_worker and self._delete_worker.is_alive():
            try:
                self._delete_stop_event.set()
                self._log("Yêu cầu dừng xóa tuần…", "warn")
            except Exception:
                pass
            return
        # Stop detect PPCT worker
        if self._detect_worker and self._detect_worker.is_alive():
            try:
                self._detect_stop.set()
                self._log("Yêu cầu dừng phát hiện PPCT…", "warn")
            except Exception:
                pass
            return
        # Không có worker nào → coi Esc như "cancel grid interaction":
        # cancel drag đang stuck + clear selection. UX tương tự Excel.
        try:
            had_drag = self._drag_state is not None
            had_selection = self._selected_slot_key is not None
            self._cancel_drag_if_stuck()
            if had_selection:
                self._set_selected_slot(None)
            if had_drag or had_selection:
                # Trả về None để các handler khác (nếu có) tiếp tục xử lý
                # — không return "break".
                pass
        except Exception:
            pass

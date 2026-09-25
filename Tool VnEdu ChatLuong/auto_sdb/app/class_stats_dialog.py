"""Hộp thoại thống kê lớp và cache thống kê."""

import copy
import json
import os
import re
import threading
import time
import tkinter as tk
import unicodedata
from tkinter import messagebox, ttk

from ..cdp.bridge import ChromeBridge
from ..config import (
    CLASS_STATS_CACHE_FILE,
    CLASS_STATS_CACHE_MAX_ENTRIES,
    CLASS_STATS_CACHE_TTL_SECONDS,
)
from ..paths import TOOL_DIR


class ClassStatsDialogMixin:
    """Hộp thoại thống kê lớp và cache thống kê."""

    def _destroy_class_stats_dialog(self):
        """Đóng dialog thống kê lớp và clear references."""
        if self._class_stats_dialog and self._class_stats_dialog.winfo_exists():
            try:
                self._class_stats_dialog.destroy()
            except Exception:
                pass
        self._class_stats_dialog = None
        self._class_stats_status_label = None
        self._class_stats_result_text = None
        self._class_stats_buttons_frame = None
        self._class_stats_buttons = {}
        self._class_stats_mon_hoc_options = []
        self.cmb_stats_mon_hoc = None

    def _clear_class_stats_queue(self):
        """Xóa các message thống kê lớp còn tồn trong queue trước phiên mới."""
        try:
            while not self._class_stats_queue.empty():
                self._class_stats_queue.get_nowait()
        except Exception:
            pass

    def _set_class_stats_status(self, text, color="#555"):
        """Cập nhật dòng trạng thái trong dialog thống kê lớp."""
        if self._class_stats_status_label and self._class_stats_status_label.winfo_exists():
            self._class_stats_status_label.config(text=text, foreground=color)

    def _ensure_class_stats_dialog(self):
        """Tạo hoặc focus dialog thống kê lớp."""
        if self._class_stats_dialog and self._class_stats_dialog.winfo_exists():
            self._class_stats_dialog.deiconify()
            self._class_stats_dialog.lift()
            self._class_stats_dialog.focus_force()
            return self._class_stats_dialog

        dlg = tk.Toplevel(self.root)
        dlg.title("Thống kê lớp & PPCT")
        dlg.transient(self.root)
        dlg.geometry("1040x700")
        dlg.minsize(860, 560)
        dlg.protocol("WM_DELETE_WINDOW", self._destroy_class_stats_dialog)
        self._class_stats_dialog = dlg

        header = ttk.Frame(dlg, padding=10)
        header.pack(fill="x")
        ttk.Label(
            header,
            text="Thống kê tình trạng sổ đầu bài theo lớp",
            font=("Segoe UI", 12, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            header,
            text=(
                "Chọn khoảng tuần để nạp đúng danh sách lớp. "
                "Nhóm PPCT dùng Môn học đã chọn; nhóm 'GV chưa nhập' và 'Row đỏ KHBD' không phụ thuộc combobox Môn học."
            ),
            foreground="#666",
            wraplength=980,
            justify="left",
        ).pack(anchor="w", pady=(2, 0))

        row_range = ttk.Frame(dlg, padding=(10, 0, 10, 6))
        row_range.pack(fill="x")
        ttk.Label(row_range, text="Tuần từ:").pack(side="left")
        ttk.Spinbox(
            row_range, from_=1, to=52, width=5, textvariable=self.var_stats_tuan_from
        ).pack(side="left", padx=4)
        ttk.Label(row_range, text="→").pack(side="left", padx=2)
        ttk.Spinbox(
            row_range, from_=1, to=52, width=5, textvariable=self.var_stats_tuan_to
        ).pack(side="left", padx=4)
        ttk.Button(
            row_range,
            text="↻ Tải lại DS lớp",
            command=self._load_class_stats_options,
        ).pack(side="right")

        row_subject = ttk.Frame(dlg, padding=(10, 0, 10, 6))
        row_subject.pack(fill="x")
        ttk.Label(row_subject, text="Môn học (chỉ cho PPCT):").pack(side="left")
        self.cmb_stats_mon_hoc = ttk.Combobox(
            row_subject,
            textvariable=self.var_stats_mon_hoc,
            state="readonly",
            width=34,
        )
        self.cmb_stats_mon_hoc.pack(side="left", padx=4, fill="x", expand=True)

        row_actions = ttk.Frame(dlg, padding=(10, 0, 10, 8))
        row_actions.pack(fill="x")
        ttk.Label(row_actions, text="PPCT:").pack(side="left")
        self.btn_stats_all_classes = ttk.Button(
            row_actions,
            text="⚡ PPCT cao nhất các lớp",
            command=self._on_run_all_class_stats,
        )
        self.btn_stats_all_classes.pack(side="left", padx=(6, 0))
        self.btn_stats_all_missing = ttk.Button(
            row_actions,
            text="🔎 Thiếu PPCT toàn lớp",
            command=self._on_run_all_class_missing,
        )
        self.btn_stats_all_missing.pack(side="left", padx=(6, 0))
        ttk.Separator(row_actions, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Label(row_actions, text="Theo màn thống kê:").pack(side="left")
        self.btn_stats_missing_teachers = ttk.Button(
            row_actions,
            text="👤 GV chưa nhập theo tuần/lớp",
            command=self._on_run_missing_teacher_audit,
        )
        self.btn_stats_missing_teachers.pack(side="left", padx=(6, 0))
        ttk.Separator(row_actions, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Label(row_actions, text="Theo KHBD:").pack(side="left")
        self.btn_stats_khdh_pending = ttk.Button(
            row_actions,
            text="📝 Row đỏ KHBD chưa lên",
            command=self._on_run_khdh_pending_audit,
        )
        self.btn_stats_khdh_pending.pack(side="left", padx=(6, 0))

        self._class_stats_status_label = ttk.Label(
            dlg,
            text="Chưa quét danh sách lớp và môn học",
            foreground="#555",
            padding=(10, 0, 10, 6),
        )
        self._class_stats_status_label.pack(fill="x")

        classes_frame = ttk.LabelFrame(dlg, text="Lớp hiện có trên sổ đầu bài", padding=6)
        classes_frame.pack(fill="x", padx=10, pady=(0, 8))
        buttons_canvas = tk.Canvas(classes_frame, height=100, highlightthickness=0)
        buttons_scroll = ttk.Scrollbar(
            classes_frame, orient="vertical", command=buttons_canvas.yview
        )
        buttons_canvas.configure(yscrollcommand=buttons_scroll.set)
        buttons_scroll.pack(side="right", fill="y")
        buttons_canvas.pack(side="left", fill="both", expand=True, padx=(0, 2))

        self._class_stats_buttons_frame = ttk.Frame(buttons_canvas)
        self._class_stats_buttons_window = buttons_canvas.create_window(
            (0, 0), window=self._class_stats_buttons_frame, anchor="nw"
        )
        self._class_stats_buttons_frame.bind(
            "<Configure>",
            lambda e: buttons_canvas.configure(scrollregion=buttons_canvas.bbox("all"))
        )
        buttons_canvas.bind(
            "<Configure>",
            lambda e: buttons_canvas.itemconfig(self._class_stats_buttons_window, width=e.width)
        )

        ttk.Label(
            self._class_stats_buttons_frame,
            text="Bấm 'Quét lớp & thống kê PPCT' hoặc 'Tải lại DS lớp' để nạp danh sách.",
            foreground="gray",
        ).grid(row=0, column=0, sticky="w", padx=4, pady=4)

        result_frame = ttk.LabelFrame(dlg, text="Kết quả thống kê", padding=6)
        result_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self._class_stats_result_text = tk.Text(
            result_frame,
            wrap="word",
            font=("Consolas", 10),
            bg="#ffffff",
            fg="#222222",
            insertbackground="#000000",
            padx=6,
            pady=6,
        )
        result_scroll = ttk.Scrollbar(
            result_frame, orient="vertical", command=self._class_stats_result_text.yview
        )
        self._class_stats_result_text.configure(yscrollcommand=result_scroll.set)
        self._class_stats_result_text.pack(side="left", fill="both", expand=True)
        result_scroll.pack(side="right", fill="y")
        self._class_stats_result_text.tag_configure("title", font=("Segoe UI", 10, "bold"))
        self._class_stats_result_text.tag_configure("ok", foreground="#1f7a1f")
        self._class_stats_result_text.tag_configure("warn", foreground="#a86400")
        self._class_stats_result_text.tag_configure("error", foreground="#b00020")
        self._class_stats_result_text.tag_configure("muted", foreground="#666666")
        self._class_stats_result_text.tag_configure("section", font=("Segoe UI", 10, "bold"), foreground="#1f2937")
        self._class_stats_result_text.insert(
            "end",
            "Kết quả sẽ hiện ở đây sau khi bạn chọn một lớp.\n",
            "muted",
        )
        self._class_stats_result_text.config(state="disabled")
        return dlg

    def _populate_class_stats_buttons(self, lop_options):
        """Hiển thị nút lớp trong dialog thống kê."""
        self._class_stats_lop_options = list(lop_options)
        if not self._class_stats_buttons_frame or not self._class_stats_buttons_frame.winfo_exists():
            return

        for widget in self._class_stats_buttons_frame.winfo_children():
            widget.destroy()
        self._class_stats_buttons = {}

        if not lop_options:
            ttk.Label(
                self._class_stats_buttons_frame,
                text="Không có lớp nào khả dụng.",
                foreground="gray",
            ).grid(row=0, column=0, sticky="w", padx=4, pady=4)
            return

        cols = 6
        for idx, lop_text in enumerate(lop_options):
            btn = ttk.Button(
                self._class_stats_buttons_frame,
                text=lop_text,
                command=lambda value=lop_text: self._on_run_class_stats(value),
            )
            btn.grid(row=idx // cols, column=idx % cols, sticky="ew", padx=3, pady=3)
            self._class_stats_buttons[lop_text] = btn

        for col_idx in range(cols):
            self._class_stats_buttons_frame.grid_columnconfigure(col_idx, weight=1)

    def _populate_class_stats_mon_hoc(self, mon_hoc_options):
        """Populate combobox Môn học cho module thống kê lớp."""
        self._class_stats_mon_hoc_options = list(mon_hoc_options or [])
        if not self.cmb_stats_mon_hoc or not self.cmb_stats_mon_hoc.winfo_exists():
            return ""

        preferred_text = self.var_stats_mon_hoc.get() or self.var_sched_mon_hoc.get()
        return self._set_sched_combobox_selection(
            self.cmb_stats_mon_hoc,
            self.var_stats_mon_hoc,
            self._class_stats_mon_hoc_options,
            preferred_text=preferred_text,
        )

    @staticmethod
    def _normalize_class_stats_subject(text):
        """Chuẩn hóa text môn học để so khớp ổn định giữa bảng và dropdown."""
        value = str(text or "").replace("\n", " ")
        value = unicodedata.normalize("NFD", value)
        value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
        value = re.sub(r"\s+", " ", value).strip().casefold()
        return value

    @classmethod
    def _get_class_stats_subject_candidates(cls, text):
        """Sinh các dạng text môn học có thể match được trên bảng."""
        raw = re.sub(r"\s+", " ", str(text or "").replace("\n", " ")).strip()
        variants = [raw]
        base_text = re.sub(r"\s*\([^)]*\)\s*", " ", raw).strip()
        if base_text:
            variants.append(base_text)

        normalized = []
        for item in variants:
            key = cls._normalize_class_stats_subject(item)
            if key and key not in normalized:
                normalized.append(key)
        return normalized

    def _class_stats_row_matches_subject(self, row_mon_hoc, target_mon_hoc):
        """Kiểm tra row bảng có thuộc môn học người dùng đang thống kê hay không."""
        target_key = self._normalize_class_stats_subject(target_mon_hoc)
        if not target_key:
            return False
        return target_key in self._get_class_stats_subject_candidates(row_mon_hoc)

    def _class_stats_cache_key(self, lop_text, tuan_num):
        """Sinh cache key ổn định theo lớp và tuần."""
        return (str(lop_text).strip().lower(), int(tuan_num))

    @staticmethod
    def _is_class_stats_cache_fresh(saved_at, now_ts=None):
        """Kiểm tra entry cache còn trong TTL hay không."""
        try:
            saved_ts = float(saved_at or 0)
        except Exception:
            return False
        if saved_ts <= 0:
            return False
        current_ts = float(now_ts if now_ts is not None else time.time())
        return (current_ts - saved_ts) <= float(CLASS_STATS_CACHE_TTL_SECONDS)

    def _class_stats_cache_file_path(self):
        """Đường dẫn file sidecar cache PPCT."""
        return os.path.join(str(TOOL_DIR), CLASS_STATS_CACHE_FILE)

    def _read_class_stats_cache_entry_locked(self, cache_key, now_ts=None):
        """Đọc cache payload theo key (yêu cầu đã giữ lock)."""
        payload = self._class_stats_fetch_cache.get(cache_key)
        if payload is None:
            return None
        saved_at = self._class_stats_fetch_cache_saved_at.get(cache_key)
        if not self._is_class_stats_cache_fresh(saved_at, now_ts=now_ts):
            self._class_stats_fetch_cache.pop(cache_key, None)
            self._class_stats_fetch_cache_saved_at.pop(cache_key, None)
            return None
        return copy.deepcopy(payload)

    def _write_class_stats_cache_entry_locked(self, cache_key, payload, saved_at=None):
        """Ghi cache payload theo key (yêu cầu đã giữ lock)."""
        payload_copy = copy.deepcopy(payload)
        self._class_stats_fetch_cache[cache_key] = payload_copy
        self._class_stats_fetch_cache_saved_at[cache_key] = float(
            saved_at if saved_at is not None else time.time()
        )
        return payload_copy

    def _save_class_stats_cache_to_disk(self):
        """Lưu cache PPCT xuống sidecar JSON để tái sử dụng ở lần mở app kế tiếp."""
        try:
            now_ts = time.time()
            entries = []
            with self._class_stats_fetch_cache_lock:
                for cache_key, payload in self._class_stats_fetch_cache.items():
                    saved_at = self._class_stats_fetch_cache_saved_at.get(cache_key)
                    if not self._is_class_stats_cache_fresh(saved_at, now_ts=now_ts):
                        continue
                    lop_key, tuan_num = cache_key
                    entries.append({
                        "lop": str(lop_key),
                        "tuan": int(tuan_num),
                        "saved_at": float(saved_at),
                        "payload": copy.deepcopy(payload),
                    })
            entries.sort(key=lambda item: float(item.get("saved_at", 0.0)), reverse=True)
            entries = entries[:CLASS_STATS_CACHE_MAX_ENTRIES]
            payload = {
                "version": 1,
                "ttl_seconds": int(CLASS_STATS_CACHE_TTL_SECONDS),
                "saved_at": now_ts,
                "entries": entries,
            }
            cache_path = self._class_stats_cache_file_path()
            self._write_json_atomic(cache_path, payload)
        except Exception as e:
            self._log(f"Lỗi save cache PPCT: {e}", "warning")

    def _load_class_stats_cache_from_disk(self):
        """Nạp cache PPCT từ sidecar JSON, tự bỏ entry hết hạn."""
        cache_path = self._class_stats_cache_file_path()
        if not os.path.exists(cache_path):
            return
        try:
            with open(cache_path, "r", encoding="utf-8") as cache_file:
                raw = json.load(cache_file)
        except Exception as e:
            self._log(f"Lỗi load cache PPCT: {e}", "warning")
            return

        entries = list((raw or {}).get("entries") or [])
        now_ts = time.time()
        loaded = 0
        with self._class_stats_fetch_cache_lock:
            self._class_stats_fetch_cache.clear()
            self._class_stats_fetch_cache_saved_at.clear()
            for item in entries:
                try:
                    lop_key = str(item.get("lop", "")).strip().lower()
                    tuan_num = int(item.get("tuan", 0))
                    saved_at = float(item.get("saved_at", 0))
                    payload = item.get("payload")
                except Exception:
                    continue
                if not lop_key or tuan_num < 1 or payload is None:
                    continue
                if not self._is_class_stats_cache_fresh(saved_at, now_ts=now_ts):
                    continue
                cache_key = (lop_key, tuan_num)
                self._write_class_stats_cache_entry_locked(cache_key, payload, saved_at=saved_at)
                loaded += 1
        if loaded:
            self._log(f"Đã nạp cache PPCT: {loaded} tuần (TTL {int(CLASS_STATS_CACHE_TTL_SECONDS/3600)}h).", "info")

    def _invalidate_class_stats_cache(self):
        """Xóa cache thống kê lớp trong session hiện tại."""
        with self._class_stats_fetch_cache_lock:
            self._class_stats_fetch_cache.clear()
            self._class_stats_fetch_cache_saved_at.clear()

    def _fetch_class_stats_week_payload(self, bridge, lop_text, tuan_num, class_meta=None):
        """Lấy payload sổ đầu bài theo lớp/tuần với cache và retry ngắn."""
        cache_key = self._class_stats_cache_key(lop_text, tuan_num)
        now_ts = time.time()
        with self._class_stats_fetch_cache_lock:
            cached_payload = self._read_class_stats_cache_entry_locked(cache_key, now_ts=now_ts)
        if cached_payload is not None:
            return True, copy.deepcopy(cached_payload), {
                "from_cache": True,
                "attempts": 0,
                "timeout_s": 0.0,
            }

        attempts = (5.5, 9.0)
        last_error = "Không lấy được dữ liệu tuần"
        for idx, timeout_s in enumerate(attempts, start=1):
            ok, payload = bridge.fetch_sodaubai_rows(
                lop_text,
                tuan_num,
                timeout_s=timeout_s,
                class_meta=class_meta,
            )
            if ok:
                fetched_week = payload.get("week")
                if fetched_week is not None and int(fetched_week) != int(tuan_num):
                    last_error = (
                        f"Service trả về tuần {fetched_week}, "
                        f"không khớp tuần yêu cầu {tuan_num}"
                    )
                    continue
                payload_copy = copy.deepcopy(payload)
                with self._class_stats_fetch_cache_lock:
                    self._write_class_stats_cache_entry_locked(cache_key, payload_copy)
                return True, copy.deepcopy(payload_copy), {
                    "from_cache": False,
                    "attempts": idx,
                    "timeout_s": timeout_s,
                }
            last_error = str(payload)

        return False, last_error, {
            "from_cache": False,
            "attempts": len(attempts),
            "timeout_s": attempts[-1],
        }

    @staticmethod
    def _resolve_class_stats_bulk_concurrency(requested_concurrency, missing_week_count):
        """Chọn concurrency an toàn cho bulk fetch để giảm abort khi quét dải tuần dài."""
        requested = max(1, int(requested_concurrency or 1))
        missing_count = max(0, int(missing_week_count or 0))
        if missing_count <= 0:
            return 1
        if missing_count >= 30:
            ceiling = 2
        elif missing_count >= 16:
            ceiling = 3
        elif missing_count >= 8:
            ceiling = 4
        else:
            ceiling = 6
        return max(1, min(requested, ceiling, missing_count))

    def _fetch_class_stats_week_payloads_bulk(self, bridge, lop_text, week_numbers, concurrency=6,
                                              class_meta=None):
        """Lấy nhiều payload lớp/tuần với cache trước, bulk fetch sau, fallback tuần tự nếu cần."""
        ordered_weeks = []
        seen = set()
        for item in list(week_numbers or []):
            try:
                week_num = int(item)
            except Exception:
                continue
            if week_num < 1 or week_num in seen:
                continue
            seen.add(week_num)
            ordered_weeks.append(week_num)

        payload_map = {}
        week_errors = []
        cache_hits = 0
        bulk_hits = 0
        fallback_hits = 0
        missing_weeks = []
        requested_concurrency = max(1, int(concurrency or 1))
        effective_concurrency = 0

        now_ts = time.time()
        with self._class_stats_fetch_cache_lock:
            for week_num in ordered_weeks:
                cache_key = self._class_stats_cache_key(lop_text, week_num)
                cached_payload = self._read_class_stats_cache_entry_locked(cache_key, now_ts=now_ts)
                if cached_payload is not None:
                    payload_map[week_num] = copy.deepcopy(cached_payload)
                    cache_hits += 1
                else:
                    missing_weeks.append(week_num)

        failed_weeks = {}
        if missing_weeks:
            effective_concurrency = self._resolve_class_stats_bulk_concurrency(
                requested_concurrency,
                len(missing_weeks),
            )
            bulk_timeout = max(12.0, min(28.0, 5.0 + len(missing_weeks) * 0.55))
            ok_bulk, bulk_payload_or_error = bridge.fetch_sodaubai_rows_bulk(
                lop_text,
                missing_weeks,
                timeout_s=bulk_timeout,
                concurrency=effective_concurrency,
                class_meta=class_meta,
            )
            if ok_bulk:
                for item in list((bulk_payload_or_error or {}).get("results") or []):
                    try:
                        requested_week = int(item.get("requested_week"))
                    except Exception:
                        continue
                    if item.get("ok"):
                        payload = copy.deepcopy(item.get("payload") or {})
                        payload_map[requested_week] = payload
                        with self._class_stats_fetch_cache_lock:
                            self._write_class_stats_cache_entry_locked(
                                self._class_stats_cache_key(lop_text, requested_week),
                                payload,
                            )
                        bulk_hits += 1
                    else:
                        failed_weeks[requested_week] = str(item.get("error", "Bulk fetch thất bại"))
            else:
                for week_num in missing_weeks:
                    failed_weeks[week_num] = str(bulk_payload_or_error)

        for week_num in missing_weeks:
            if week_num in payload_map:
                continue
            ok, payload_or_error, _meta = self._fetch_class_stats_week_payload(
                bridge,
                lop_text,
                week_num,
                class_meta=class_meta,
            )
            if ok:
                payload_map[week_num] = payload_or_error
                fallback_hits += 1
            else:
                week_errors.append({
                    "week": week_num,
                    "message": failed_weeks.get(week_num, str(payload_or_error)),
                })

        return payload_map, week_errors, {
            "cache_hits": cache_hits,
            "bulk_hits": bulk_hits,
            "fallback_hits": fallback_hits,
            "requested_concurrency": requested_concurrency,
            "effective_concurrency": effective_concurrency,
        }

    def _extract_class_stats_subject_rows(self, table_rows, tuan_num, target_mon_hoc):
        """Lọc các row thuộc môn đã chọn và trích xuất occurrence/PPCT."""
        tuan_text = f"Tuần {tuan_num}"
        occurrences = []
        invalid_ppct_rows = []
        total_rows_with_data = 0

        for row in list(table_rows or []):
            if not row.get("has_data"):
                continue

            slot_label = self._format_schedule_slot_label(
                row.get("thu", "?"),
                row.get("buoi", "?"),
                row.get("tiet", "?"),
            )
            ppct_text = str(row.get("ppct", "")).strip()
            mon_hoc = str(row.get("mon_hoc", "")).strip() or "(Không rõ môn)"
            if not self._class_stats_row_matches_subject(mon_hoc, target_mon_hoc):
                continue

            total_rows_with_data += 1
            occurrence = {
                "week": tuan_num,
                "week_text": tuan_text,
                "slot_label": slot_label,
                "ppct_text": ppct_text,
                "mon_hoc": mon_hoc,
            }

            match = re.search(r"\d+", ppct_text)
            if not match:
                invalid_ppct_rows.append({
                    **occurrence,
                    "message": "Có dữ liệu môn học nhưng PPCT trống hoặc không hợp lệ",
                })
                continue

            occurrence["ppct"] = int(match.group())
            occurrences.append(occurrence)

        return occurrences, invalid_ppct_rows, total_rows_with_data

    def _load_class_stats_options(self):
        """Tải danh sách lớp và tuần thực tế từ VnEdu để phục vụ thống kê."""
        if not self._cdp_connected:
            self._log("Chưa kết nối CDP!", "error")
            messagebox.showwarning("Cảnh báo", "Hãy kết nối CDP trước khi quét lớp.")
            return
        if self._class_stats_thread and self._class_stats_thread.is_alive():
            self._log("Đang có một tác vụ thống kê lớp chạy rồi.", "warning")
            return
        if self._schedule_running:
            messagebox.showwarning(
                "Cảnh báo",
                "Schedule đang chạy. Hãy dừng hoặc chờ xong rồi mới quét thống kê lớp.",
            )
            return
        self._ensure_class_stats_dialog()
        self._clear_class_stats_queue()
        tuan_nums = self._week_numbers_from_vars(
            self.var_stats_tuan_from,
            self.var_stats_tuan_to,
            require_multi_week=True,
        )
        scan_scope = (
            f"Tuần {tuan_nums[0]}→{tuan_nums[-1]}" if tuan_nums else "toàn bộ tuần có trên web"
        )
        self._set_class_stats_status(
            f"⏳ Đang tải danh sách lớp và tuần ({scan_scope})...",
            "#a86400",
        )
        cached_mon_hoc_options = copy.deepcopy(self._sched_mon_hoc_options)

        def _work():
            try:
                bridge = ChromeBridge(port=self._cdp_port)
                ok, msg = bridge.connect()
                if not ok:
                    self._class_stats_queue.put(("options_error", f"Kết nối CDP thất bại: {msg}"))
                    return
                ok_lop, lop_data = bridge.discover_lop_options_for_weeks(tuan_nums=tuan_nums)
                ok_tuan, tuan_data = bridge.get_tuan_options()
                ok_mon, mon_data = bridge.read_form_options(row_index=0)
                bridge.disconnect()
                if not ok_lop:
                    self._class_stats_queue.put(("options_error", f"Lỗi tải lớp: {lop_data}"))
                    return
                if not ok_tuan:
                    self._class_stats_queue.put(("options_error", f"Lỗi tải tuần: {tuan_data}"))
                    return
                mon_hoc_options = []
                mon_hoc_note = ""
                if ok_mon:
                    mon_hoc_options = list((mon_data or {}).get("mon_hoc", []) or [])
                elif cached_mon_hoc_options:
                    mon_hoc_options = list(cached_mon_hoc_options)
                    mon_hoc_note = f"Không quét được danh sách môn từ form, dùng cache cũ: {mon_data}"
                else:
                    self._class_stats_queue.put(
                        ("options_error", f"Lỗi tải môn học từ form: {mon_data}")
                    )
                    return
                self._class_stats_queue.put(("options_ready", {
                    "lop_options": self._sort_lop_options(lop_data.get("options", [])),
                    "lop_records": list(lop_data.get("records", []) or []),
                    "tuan_options": tuan_data,
                    "mon_hoc_options": mon_hoc_options,
                    "mon_hoc_note": mon_hoc_note,
                    "class_scan_scope": scan_scope,
                    "class_weeks_scanned": len(lop_data.get("weeks_scanned", []) or []),
                }))
            except Exception as e:
                self._class_stats_queue.put(("options_error", f"Exception tải lớp: {e}"))

        self._class_stats_thread = threading.Thread(target=_work, daemon=True)
        self._class_stats_thread.start()
        self.root.after(100, self._poll_class_stats_queue)

    def _on_open_class_stats_dialog(self):
        """Mở dialog thống kê lớp và quét danh sách lớp hiện có."""
        self._ensure_class_stats_dialog()
        self._load_class_stats_options()

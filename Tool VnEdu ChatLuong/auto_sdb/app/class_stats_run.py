"""Chạy thống kê lớp / GV chưa nhập / KHDH chờ."""

import copy
import re
import threading
import tkinter as tk
from tkinter import messagebox

from ..cdp.bridge import ChromeBridge


class ClassStatsRunMixin:
    """Chạy thống kê lớp / GV chưa nhập / KHDH chờ."""

    def _set_class_stats_running(self, running, selected_lop=None):
        """Bật/tắt trạng thái chạy cho cụm thống kê lớp."""
        self._class_stats_running = running
        self._class_stats_selected_lop = selected_lop
        for lop_text, btn in self._class_stats_buttons.items():
            btn.config(state="disabled" if running else "normal")
        if getattr(self, "btn_stats_all_classes", None) is not None:
            try:
                self.btn_stats_all_classes.config(state="disabled" if running else "normal")
            except Exception:
                pass
        if getattr(self, "btn_stats_all_missing", None) is not None:
            try:
                self.btn_stats_all_missing.config(state="disabled" if running else "normal")
            except Exception:
                pass
        if getattr(self, "btn_stats_missing_teachers", None) is not None:
            try:
                self.btn_stats_missing_teachers.config(state="disabled" if running else "normal")
            except Exception:
                pass
        if getattr(self, "btn_stats_khdh_pending", None) is not None:
            try:
                self.btn_stats_khdh_pending.config(state="disabled" if running else "normal")
            except Exception:
                pass
        if getattr(self, "cmb_stats_mon_hoc", None) is not None:
            try:
                self.cmb_stats_mon_hoc.config(state="disabled" if running else "readonly")
            except Exception:
                pass

    def _get_class_stats_request_context(self, require_subject=True):
        """Đọc và validate context chung cho mọi thao tác thống kê lớp."""
        if not self._cdp_connected:
            self._log("Chưa kết nối CDP!", "error")
            return None
        if self._schedule_running:
            messagebox.showwarning(
                "Cảnh báo",
                "Schedule đang chạy. Hãy dừng hoặc chờ xong rồi mới thống kê lớp.",
            )
            return None
        if self._delete_running or self._delete_scanning:
            messagebox.showwarning(
                "Cảnh báo",
                "Đang có thao tác Xóa dữ liệu chạy. Hãy chờ xong rồi mới thống kê lớp.",
            )
            return None
        if self._class_stats_running:
            self._log("Đang có một phiên thống kê lớp chạy rồi.", "warning")
            return None
        if self._class_stats_thread and self._class_stats_thread.is_alive():
            self._log("Đang có một tác vụ thống kê lớp khác đang khởi tạo.", "warning")
            return None

        mon_hoc_opt = None
        if require_subject:
            mon_hoc_opt = self._resolve_sched_option(
                self._class_stats_mon_hoc_options,
                self.var_stats_mon_hoc.get(),
            )
        if require_subject and mon_hoc_opt is None:
            messagebox.showwarning(
                "Cảnh báo",
                "Hãy chọn Môn học cần thống kê trước khi bấm vào lớp.",
            )
            return None

        try:
            tuan_from = int(self.var_stats_tuan_from.get())
            tuan_to = int(self.var_stats_tuan_to.get())
        except (tk.TclError, ValueError):
            messagebox.showwarning("Cảnh báo", "Tuần thống kê không hợp lệ.")
            return None
        if tuan_from > tuan_to:
            tuan_from, tuan_to = tuan_to, tuan_from

        self.var_stats_tuan_from.set(tuan_from)
        self.var_stats_tuan_to.set(tuan_to)
        return {
            "tuan_from": tuan_from,
            "tuan_to": tuan_to,
            "mon_hoc": mon_hoc_opt["text"] if mon_hoc_opt else "",
        }

    def _on_run_class_stats(self, lop_text):
        """Phân tích dữ liệu PPCT cho một lớp trong khoảng tuần được chọn."""
        context = self._get_class_stats_request_context()
        if context is None:
            return

        self._clear_class_stats_queue()
        self._set_class_stats_running(True, lop_text)
        self._set_class_stats_status(
            f"⏳ Đang quét lớp {lop_text} | Môn {context['mon_hoc']} | "
            f"Tuần {context['tuan_from']}→{context['tuan_to']}...",
            "#a86400",
        )
        self._log(
            f"📊 Thống kê lớp bắt đầu: {lop_text}, Môn {context['mon_hoc']}, "
            f"Tuần {context['tuan_from']}→{context['tuan_to']}",
            "info",
        )

        record_map = self._get_class_stats_record_map()
        params = {
            "port": self._cdp_port,
            "lop": lop_text,
            "class_meta": copy.deepcopy(record_map.get(str(lop_text).strip().lower()) or {}),
            "tuan_from": context["tuan_from"],
            "tuan_to": context["tuan_to"],
            "mon_hoc": context["mon_hoc"],
            "scan_mode": "fast_max",
        }
        self._class_stats_thread = threading.Thread(
            target=self._class_stats_worker,
            args=(params,),
            daemon=True,
        )
        self._class_stats_thread.start()
        self.root.after(100, self._poll_class_stats_queue)

    def _on_run_all_class_stats(self):
        """Tổng hợp nhanh PPCT cao nhất cho toàn bộ lớp đang có theo môn đã chọn."""
        context = self._get_class_stats_request_context()
        if context is None:
            return
        lop_options = [x for x in self._class_stats_lop_options if str(x).strip()]
        if not lop_options:
            messagebox.showwarning(
                "Cảnh báo",
                "Chưa có danh sách lớp. Hãy bấm tải danh sách lớp trước.",
            )
            return

        self._clear_class_stats_queue()
        self._set_class_stats_running(True, "ALL_CLASSES")
        self._set_class_stats_status(
            f"⚡ Đang quét nhanh {len(lop_options)} lớp | Môn {context['mon_hoc']} | "
            f"Tuần {context['tuan_from']}→{context['tuan_to']}...",
            "#a86400",
        )
        self._log(
            f"⚡ Bắt đầu quét nhanh PPCT cao nhất: {len(lop_options)} lớp, "
            f"Môn {context['mon_hoc']}, Tuần {context['tuan_from']}→{context['tuan_to']}",
            "info",
        )

        record_map = self._get_class_stats_record_map()
        params = {
            "port": self._cdp_port,
            "lop_list": lop_options,
            "class_records": [
                copy.deepcopy(record_map.get(str(lop).strip().lower()) or {"text": str(lop).strip()})
                for lop in lop_options
            ],
            "tuan_from": context["tuan_from"],
            "tuan_to": context["tuan_to"],
            "mon_hoc": context["mon_hoc"],
        }
        self._class_stats_thread = threading.Thread(
            target=self._class_stats_all_worker,
            args=(params,),
            daemon=True,
        )
        self._class_stats_thread.start()
        self.root.after(100, self._poll_class_stats_queue)

    def _on_run_all_class_missing(self):
        """Quét FULL toàn bộ lớp để phát hiện lớp thiếu/trùng PPCT trong khoảng tuần."""
        context = self._get_class_stats_request_context()
        if context is None:
            return
        lop_options = [x for x in self._class_stats_lop_options if str(x).strip()]
        if not lop_options:
            messagebox.showwarning(
                "Cảnh báo",
                "Chưa có danh sách lớp. Hãy bấm tải danh sách lớp trước.",
            )
            return

        self._clear_class_stats_queue()
        self._set_class_stats_running(True, "ALL_CLASSES_FULL")
        self._set_class_stats_status(
            f"🔎 Đang quét FULL {len(lop_options)} lớp | Môn {context['mon_hoc']} | "
            f"Tuần {context['tuan_from']}→{context['tuan_to']}...",
            "#a86400",
        )
        self._log(
            f"🔎 Bắt đầu quét thiếu PPCT toàn lớp: {len(lop_options)} lớp, "
            f"Môn {context['mon_hoc']}, Tuần {context['tuan_from']}→{context['tuan_to']}",
            "info",
        )

        record_map = self._get_class_stats_record_map()
        params = {
            "port": self._cdp_port,
            "lop_list": lop_options,
            "class_records": [
                copy.deepcopy(record_map.get(str(lop).strip().lower()) or {"text": str(lop).strip()})
                for lop in lop_options
            ],
            "tuan_from": context["tuan_from"],
            "tuan_to": context["tuan_to"],
            "mon_hoc": context["mon_hoc"],
            "scan_mode": "full_missing",
        }
        self._class_stats_thread = threading.Thread(
            target=self._class_stats_all_worker,
            args=(params,),
            daemon=True,
        )
        self._class_stats_thread.start()
        self.root.after(100, self._poll_class_stats_queue)

    @staticmethod
    def _infer_grade_label_from_class_text(lop_text):
        """Suy ra nhãn 'Khối X' từ tên lớp như 6A4."""
        match = re.match(r"\s*(\d{1,2})", str(lop_text or "").strip())
        if not match:
            return ""
        return f"Khối {int(match.group(1))}"

    def _get_class_stats_record_map(self):
        """Map text lớp -> metadata class_id/khoi/cap đã quét được."""
        record_map = {}
        for item in list(self._class_stats_lop_records or []):
            text = str(item.get("text", "") or "").strip()
            if not text:
                continue
            key = text.lower()
            current = record_map.get(key, {"text": text, "value": "", "khoi": "", "cap": ""})
            for field in ("value", "khoi", "cap", "source"):
                incoming = str(item.get(field, "") or "").strip()
                if incoming and not current.get(field):
                    current[field] = incoming
            if not current.get("khoi"):
                current["khoi"] = re.sub(r"^Khối\s*", "", self._infer_grade_label_from_class_text(text), flags=re.I)
            record_map[key] = current
        return record_map

    def _resolve_missing_teacher_slots(self, stats_rows, detail_rows):
        """Map dữ liệu thiếu GV theo bucket Thứ/Buổi sang các tiết trống trong bảng chi tiết."""
        bucket_empty = {}
        detail_rows = list(detail_rows or [])
        has_schedule_classification = any("is_scheduled" in row for row in detail_rows)
        for row in detail_rows:
            if row.get("has_data"):
                continue
            if has_schedule_classification and not row.get("is_scheduled"):
                continue
            tiet_text = str(row.get("tiet", "") or "").strip()
            if not tiet_text.isdigit():
                continue
            thu_key = ChromeBridge._normalize_thu_token(row.get("thu", ""))
            buoi_key = ChromeBridge._normalize_buoi_token(row.get("buoi", ""))
            if not thu_key or not buoi_key:
                continue
            bucket_empty.setdefault((thu_key, buoi_key), []).append({
                "thu": str(row.get("thu", "") or "").strip(),
                "buoi": str(row.get("buoi", "") or "").strip(),
                "tiet": tiet_text,
                "slot_label": self._format_schedule_slot_label(
                    row.get("thu", "?"),
                    row.get("buoi", "?"),
                    row.get("tiet", "?"),
                ),
            })

        for rows in bucket_empty.values():
            rows.sort(key=lambda item: int(item.get("tiet", 0) or 0))

        resolved = []
        bucket_demands = {}
        for idx, stats_row in enumerate(list(stats_rows or [])):
            teacher_payload = {
                "teacher_name": str(stats_row.get("teacher_name", "") or "").strip(),
                "mon_hoc": str(stats_row.get("mon_hoc", "") or "").strip(),
                "lop": str(stats_row.get("lop", "") or "").strip(),
                "total_missing": int(stats_row.get("total_missing", 0) or 0),
                "exact_slots": [],
                "ambiguous_buckets": [],
                "unmatched_buckets": [],
                "raw_counts": copy.deepcopy(list(stats_row.get("counts") or [])),
            }
            resolved.append(teacher_payload)
            for count_info in list(stats_row.get("counts") or []):
                try:
                    count_value = int(count_info.get("count", 0) or 0)
                except Exception:
                    count_value = 0
                if count_value <= 0:
                    continue
                thu = str(count_info.get("thu", "") or "").strip()
                buoi = str(count_info.get("buoi", "") or "").strip()
                key = (
                    ChromeBridge._normalize_thu_token(thu),
                    ChromeBridge._normalize_buoi_token(buoi),
                )
                bucket_demands.setdefault(key, []).append({
                    "row_index": idx,
                    "teacher_name": teacher_payload["teacher_name"],
                    "mon_hoc": teacher_payload["mon_hoc"],
                    "lop": teacher_payload["lop"],
                    "thu": thu,
                    "buoi": buoi,
                    "count": count_value,
                })

        for bucket_key, demands in bucket_demands.items():
            empty_rows = list(bucket_empty.get(bucket_key, []) or [])
            total_demand = sum(int(item.get("count", 0) or 0) for item in demands)
            unique_teachers = {
                (item.get("teacher_name", ""), item.get("mon_hoc", ""))
                for item in demands
            }
            if not empty_rows:
                for demand in demands:
                    resolved[demand["row_index"]]["unmatched_buckets"].append({
                        "thu": demand["thu"],
                        "buoi": demand["buoi"],
                        "count": demand["count"],
                        "note": "Bảng thống kê báo thiếu nhưng không tìm thấy tiết trống tương ứng trong bảng chi tiết.",
                    })
                continue

            if len(demands) == 1 and len(empty_rows) == total_demand:
                demand = demands[0]
                resolved[demand["row_index"]]["exact_slots"].extend(copy.deepcopy(empty_rows))
                continue

            for demand in demands:
                target = resolved[demand["row_index"]]
                target["ambiguous_buckets"].append({
                    "thu": demand["thu"],
                    "buoi": demand["buoi"],
                    "count": demand["count"],
                    "candidate_slots": copy.deepcopy(empty_rows),
                    "note": (
                        "Không phân bổ chắc chắn được tiết vì cùng bucket có nhiều tiết trống "
                        "hoặc nhiều giáo viên thiếu."
                        if len(unique_teachers) > 1 or len(empty_rows) != demand["count"]
                        else "Bucket còn nhiều tiết trống hơn số lượng thiếu."
                    ),
                })

        for item in resolved:
            item["exact_slots"].sort(key=lambda slot: (
                self._schedule_occurrence_sort_key({
                    "week": 0,
                    "thu": slot.get("thu"),
                    "buoi": slot.get("buoi"),
                    "tiet": slot.get("tiet"),
                })[1],
                self._schedule_buoi_sort_key(slot.get("buoi")),
                int(slot.get("tiet", 0) or 0),
            ))
        return resolved

    def _on_run_missing_teacher_audit(self):
        """Quét màn thống kê để tìm giáo viên chưa nhập theo tuần/lớp."""
        context = self._get_class_stats_request_context(require_subject=False)
        if context is None:
            return

        lop_options = [x for x in self._class_stats_lop_options if str(x).strip()]
        if not lop_options:
            messagebox.showwarning(
                "Cảnh báo",
                "Chưa có danh sách lớp. Hãy bấm tải danh sách lớp trước.",
            )
            return

        record_map = self._get_class_stats_record_map()
        class_records = []
        missing_meta = []
        for lop_text in lop_options:
            key = str(lop_text).strip().lower()
            record = copy.deepcopy(record_map.get(key) or {})
            if not record.get("text"):
                record["text"] = str(lop_text).strip()
            if not record.get("value"):
                missing_meta.append(lop_text)
            class_records.append(record)

        if missing_meta:
            messagebox.showwarning(
                "Cảnh báo",
                "Một số lớp chưa có metadata class_id ổn định. Hãy bấm 'Tải lại DS lớp' rồi thử lại.",
            )
            return

        self._clear_class_stats_queue()
        self._set_class_stats_running(True, "MISSING_TEACHERS")
        self._set_class_stats_status(
            f"👤 Đang quét GV chưa nhập | {len(class_records)} lớp | "
            f"Tuần {context['tuan_from']}→{context['tuan_to']}...",
            "#a86400",
        )
        self._log(
            f"👤 Bắt đầu quét GV chưa nhập theo thống kê: {len(class_records)} lớp | "
            f"Tuần {context['tuan_from']}→{context['tuan_to']}",
            "info",
        )

        params = {
            "port": self._cdp_port,
            "class_records": class_records,
            "tuan_from": context["tuan_from"],
            "tuan_to": context["tuan_to"],
            "username": self.var_vnedu_username.get().strip(),
            "password": self.var_vnedu_password.get(),
        }
        self._class_stats_thread = threading.Thread(
            target=self._class_stats_missing_teacher_worker,
            args=(params,),
            daemon=True,
        )
        self._class_stats_thread.start()
        self.root.after(100, self._poll_class_stats_queue)

    def _on_run_khdh_pending_audit(self):
        """Quét row đỏ KHDH còn treo theo lớp/tuần, không nhập dữ liệu."""
        context = self._get_class_stats_request_context(require_subject=False)
        if context is None:
            return

        lop_options = [x for x in self._class_stats_lop_options if str(x).strip()]
        if not lop_options:
            messagebox.showwarning(
                "Cảnh báo",
                "Chưa có danh sách lớp. Hãy bấm tải danh sách lớp trước.",
            )
            return

        record_map = self._get_class_stats_record_map()
        class_records = []
        for lop_text in lop_options:
            key = str(lop_text).strip().lower()
            record = copy.deepcopy(record_map.get(key) or {})
            if not record.get("text"):
                record["text"] = str(lop_text).strip()
            class_records.append(record)

        self._clear_class_stats_queue()
        self._set_class_stats_running(True, "KHDH_PENDING")
        self._set_class_stats_status(
            f"📝 Đang quét row đỏ KHBD/KHDH | {len(lop_options)} lớp | "
            f"Tuần {context['tuan_from']}→{context['tuan_to']}...",
            "#a86400",
        )
        self._log(
            f"📝 Bắt đầu quét row đỏ KHBD/KHDH: {len(lop_options)} lớp | "
            f"Tuần {context['tuan_from']}→{context['tuan_to']}",
            "info",
        )

        params = {
            "port": self._cdp_port,
            "lop_list": lop_options,
            "class_records": class_records,
            "tuan_from": context["tuan_from"],
            "tuan_to": context["tuan_to"],
            "username": self.var_vnedu_username.get().strip(),
            "password": self.var_vnedu_password.get(),
        }
        self._class_stats_thread = threading.Thread(
            target=self._class_stats_khdh_pending_worker,
            args=(params,),
            daemon=True,
        )
        self._class_stats_thread.start()
        self.root.after(100, self._poll_class_stats_queue)

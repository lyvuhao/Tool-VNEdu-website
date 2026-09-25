"""Worker thống kê chạy nền."""

import copy
import time
from concurrent.futures import as_completed, ThreadPoolExecutor

from ..cdp.bridge import ChromeBridge
from ..cdp.health import is_cdp_target_closed_error


class ClassStatsWorkersMixin:
    """Worker thống kê chạy nền."""

    def _class_stats_missing_teacher_worker(self, params):
        """Worker quét màn thống kê GV chưa nhập rồi map sang tiết trống."""
        q = self._class_stats_queue
        bridge = None
        reports = []
        issues = []
        pairs_scanned = 0
        pairs_with_missing = 0
        try:
            class_records = list(params.get("class_records") or [])
            bridge = ChromeBridge(port=params["port"])
            ok, msg = bridge.connect()
            if not ok:
                q.put(("stats_error", f"Kết nối CDP thất bại: {msg}"))
                return

            ok_stats, stats_msg = bridge.ensure_thong_ke_nhap_sodau_bai(
                params.get("username", ""),
                params.get("password", ""),
            )
            if not ok_stats:
                q.put(("stats_error", stats_msg))
                return

            class_records_by_grade = {}
            permission_scope_signatures = set()
            for class_record in class_records:
                lop_text = str(class_record.get("text", "") or "").strip()
                if not lop_text:
                    continue
                grade_text = (
                    f"Khối {class_record.get('khoi')}".strip()
                    if str(class_record.get("khoi", "") or "").strip()
                    else self._infer_grade_label_from_class_text(lop_text)
                )
                class_records_by_grade.setdefault(grade_text.casefold(), {
                    "label": grade_text,
                    "records": [],
                })["records"].append(class_record)

            for tuan_num in range(int(params["tuan_from"]), int(params["tuan_to"]) + 1):
                q.put(("stats_status", f"👤 Đang đọc thống kê GV chưa nhập — Tuần {tuan_num}..."))
                ok_week, week_msg = bridge.stats_select_week(f"Tuần {tuan_num}")
                if not ok_week:
                    issues.append({
                        "week": tuan_num,
                        "lop": "",
                        "message": f"Không chọn được tuần: {week_msg}",
                    })
                    continue

                ok_grades, grades_or_error = bridge.stats_get_filter_options("grade")
                if not ok_grades:
                    issues.append({
                        "week": tuan_num,
                        "lop": "",
                        "message": f"Không đọc được khối được cấp quyền: {grades_or_error}",
                    })
                    continue
                available_grades = {
                    str(item or "").strip().casefold(): str(item or "").strip()
                    for item in list(grades_or_error or [])
                    if str(item or "").strip()
                }
                skipped_grades = [
                    info["label"]
                    for key, info in class_records_by_grade.items()
                    if key not in available_grades
                ]
                if skipped_grades:
                    scope_signature = tuple(sorted(skipped_grades))
                    if scope_signature not in permission_scope_signatures:
                        permission_scope_signatures.add(scope_signature)
                        q.put((
                            "log",
                            "Màn thống kê VnEdu không cấp quyền các khối: "
                            + ", ".join(skipped_grades),
                            "warning",
                        ))

                for grade_key, grade_info in class_records_by_grade.items():
                    grade_text = available_grades.get(grade_key)
                    if not grade_text:
                        continue
                    ok_grade, grade_msg = bridge.stats_select_grade(grade_text)
                    if not ok_grade:
                        issues.append({
                            "week": tuan_num,
                            "lop": "",
                            "message": f"Không chọn được {grade_text}: {grade_msg}",
                        })
                        continue
                    ok_classes, classes_or_error = bridge.stats_get_filter_options("class")
                    if not ok_classes:
                        issues.append({
                            "week": tuan_num,
                            "lop": "",
                            "message": (
                                f"Không đọc được lớp được cấp quyền của {grade_text}: "
                                f"{classes_or_error}"
                            ),
                        })
                        continue
                    available_classes = {
                        str(item or "").strip().casefold(): str(item or "").strip()
                        for item in list(classes_or_error or [])
                        if str(item or "").strip()
                    }

                    for class_record in list(grade_info["records"] or []):
                        requested_lop = str(class_record.get("text", "") or "").strip()
                        lop_text = available_classes.get(requested_lop.casefold())
                        if not lop_text:
                            continue
                        q.put((
                            "stats_status",
                            f"👤 Tuần {tuan_num}: {lop_text} | {grade_text}...",
                        ))
                        ok_class, class_msg = bridge.stats_select_class(lop_text)
                        if not ok_class:
                            issues.append({
                                "week": tuan_num,
                                "lop": lop_text,
                                "message": f"Không chọn được lớp: {class_msg}",
                            })
                            continue
                        ok_toggle, toggle_msg = bridge.stats_set_missing_only(True)
                        if not ok_toggle:
                            issues.append({
                                "week": tuan_num,
                                "lop": lop_text,
                                "message": f"Không bật được lọc GV chưa nhập: {toggle_msg}",
                            })
                            continue

                        ok_stats_rows, stats_payload = bridge.stats_read_missing_teacher_rows(
                            expected_class=lop_text,
                            timeout_s=6.5,
                        )
                        pairs_scanned += 1
                        if not ok_stats_rows:
                            issues.append({
                                "week": tuan_num,
                                "lop": lop_text,
                                "message": str(stats_payload),
                            })
                            continue

                        stats_rows = list((stats_payload or {}).get("rows") or [])
                        if not stats_rows:
                            continue

                        ok_detail, detail_payload = bridge.fetch_sodaubai_rows(
                            requested_lop,
                            tuan_num,
                            timeout_s=9.5,
                            class_meta=class_record,
                            show_goi_y=True,
                        )
                        if not ok_detail:
                            issues.append({
                                "week": tuan_num,
                                "lop": lop_text,
                                "message": f"Không fetch được bảng chi tiết: {detail_payload}",
                            })
                            continue

                        pairs_with_missing += 1
                        resolved_rows = self._resolve_missing_teacher_slots(
                            stats_rows,
                            list((detail_payload or {}).get("rows") or []),
                        )
                        reports.append({
                            "week": tuan_num,
                            "week_text": f"Tuần {tuan_num}",
                            "lop": lop_text,
                            "teachers": resolved_rows,
                        })

            q.put(("missing_teacher_done", {
                "tuan_from": int(params["tuan_from"]),
                "tuan_to": int(params["tuan_to"]),
                "reports": reports,
                "issues": issues,
                "pairs_scanned": pairs_scanned,
                "pairs_with_missing": pairs_with_missing,
                "class_count": len(class_records),
            }))
        except Exception as e:
            q.put(("stats_error", f"Worker GV chưa nhập lỗi: {type(e).__name__}: {str(e)[:160]}"))
        finally:
            if bridge:
                try:
                    ok_cleanup, msg_cleanup = bridge.cleanup_after_automation(restore_view_mode=False)
                    q.put(("stats_status", f"Đã cleanup trạng thái web sau thống kê: {msg_cleanup}" if ok_cleanup else msg_cleanup))
                except Exception:
                    pass
                try:
                    bridge.disconnect()
                except Exception:
                    pass

    def _class_stats_khdh_pending_worker(self, params):
        """Worker quét KHDH chưa nhập bằng service; UI chỉ là fallback."""
        q = self._class_stats_queue
        bridge = None
        reports = []
        issues = []
        pairs_scanned = 0
        pairs_with_pending = 0
        total_rows = 0
        skipped_unavailable = 0
        try:
            lop_list = [
                str(item or "").strip()
                for item in list(params.get("lop_list") or [])
                if str(item or "").strip()
            ]
            requested_lop_keys = {item.casefold(): item for item in lop_list}
            week_numbers = list(range(
                int(params["tuan_from"]),
                int(params["tuan_to"]) + 1,
            ))
            record_map = {}
            for item in list(params.get("class_records") or []):
                text = str(item.get("text", "") or "").strip()
                if text:
                    record_map[text.casefold()] = copy.deepcopy(item)
            for lop_text in lop_list:
                record_map.setdefault(lop_text.casefold(), {"text": lop_text})

            def _fatal_if_cdp_closed(message):
                if is_cdp_target_closed_error(message):
                    q.put(("stats_error", (
                        "CDP/Chrome đã đóng hoặc tab VnEdu không còn khả dụng. "
                        "Worker đã dừng để tránh sinh lỗi lặp. Hãy mở lại/kết nối lại Chrome rồi chạy lại."
                    )))
                    return True
                return False

            def _select_dropdown_retry(label, text, attempts=3):
                last_msg = ""
                for attempt in range(max(int(attempts), 1)):
                    ok_select, msg_select = bridge.select_dropdown(label, text)
                    if ok_select:
                        return True, msg_select
                    last_msg = str(msg_select)
                    if is_cdp_target_closed_error(last_msg):
                        return False, last_msg
                    if attempt < attempts - 1:
                        time.sleep(0.35 + attempt * 0.35)
                return False, last_msg

            def _normalize_pending_rows(rows, require_schedule_flag):
                pending_rows = []
                for row in list(rows or []):
                    if row.get("has_data") or not row.get("has_add_btn"):
                        continue
                    if require_schedule_flag and not row.get("is_scheduled"):
                        continue
                    red_texts = [
                        str(value).strip()
                        for value in list(row.get("red_texts") or [])
                        if str(value).strip()
                    ]
                    pending_rows.append({
                        "slot_label": self._format_schedule_slot_label(
                            row.get("thu", "?"),
                            row.get("buoi", "?"),
                            row.get("tiet", "?"),
                        ),
                        "thu": str(row.get("thu", "") or "").strip(),
                        "buoi": str(row.get("buoi", "") or "").strip(),
                        "tiet": str(row.get("tiet", "") or "").strip(),
                        "ngay": str(row.get("ngay", "") or "").strip(),
                        "ppct_hint": str(
                            row.get("ppct_hint")
                            or row.get("tiet_ppct_attr")
                            or row.get("ppct")
                            or ""
                        ).strip(),
                        "mon_hoc_hint": str(
                            row.get("mon_hoc_text_hint")
                            or row.get("mon_hoc_hint")
                            or row.get("mon_hoc")
                            or ""
                        ).strip(),
                        "phan_mon_text_hint": str(
                            row.get("phan_mon_text_hint", "") or ""
                        ).strip(),
                        "noi_dung_hint": str(
                            row.get("noi_dung_hint")
                            or row.get("noi_dung_cong_viec")
                            or ""
                        ).strip(),
                        "red_texts": red_texts,
                    })
                return pending_rows

            def _append_report(tuan_num, lop_text, pending_rows):
                nonlocal pairs_with_pending, total_rows
                if not pending_rows:
                    return
                pairs_with_pending += 1
                total_rows += len(pending_rows)
                reports.append({
                    "week": tuan_num,
                    "week_text": f"Tuần {tuan_num}",
                    "lop": lop_text,
                    "rows": pending_rows,
                })

            def _scan_ui_fallback(tuan_num, lop_text):
                ok_tuan, msg_tuan = _select_dropdown_retry(
                    "tuan",
                    f"Tuần {tuan_num}",
                    attempts=2,
                )
                if not ok_tuan:
                    return False, [], f"Lỗi chọn tuần: {msg_tuan}", False
                ok_week_lops, week_lops_or_error = bridge.get_lop_options()
                if not ok_week_lops:
                    return False, [], (
                        f"Không đọc được danh sách lớp của tuần: {week_lops_or_error}"
                    ), False
                week_lop_map = {
                    str(item or "").strip().casefold(): str(item or "").strip()
                    for item in list(week_lops_or_error or [])
                    if str(item or "").strip()
                }
                actual_lop = week_lop_map.get(lop_text.casefold())
                if not actual_lop:
                    return True, [], "Lớp không xuất hiện ở tuần này", True
                ok_lop, msg_lop = _select_dropdown_retry("lop", actual_lop, attempts=2)
                if not ok_lop:
                    return False, [], f"Lỗi chọn lớp: {msg_lop}", False
                ok_mode, msg_mode = bridge.set_goi_y_khdh_mode(True)
                if not ok_mode:
                    return False, [], f"Không bật được Gợi ý theo KHDH: {msg_mode}", False
                ok_rows, rows_or_error = bridge.read_khdh_suggested_rows()
                if not ok_rows:
                    return False, [], f"Không đọc được row đỏ KHDH: {rows_or_error}", False
                return True, _normalize_pending_rows(rows_or_error, False), "", False

            bridge = ChromeBridge(port=params["port"])
            ok, msg = bridge.connect()
            if not ok:
                q.put(("stats_error", f"Kết nối CDP thất bại: {msg}"))
                return

            ok_ready, ready_msg = bridge.ensure_chi_tiet_sodau_bai(
                params.get("username", ""),
                params.get("password", ""),
            )
            if not ok_ready:
                q.put(("stats_error", ready_msg))
                return

            q.put(("stats_status", "📝 KHBD | Đang lấy metadata lớp thật qua service..."))
            classes_by_week = {}
            ok_discover, discover_payload = bridge.fetch_lop_options_for_weeks_service(
                week_numbers,
                timeout_s=10.0,
                concurrency=6,
            )
            if ok_discover:
                classes_by_week = dict(discover_payload.get("classes_by_week") or {})
                for item in list(discover_payload.get("records") or []):
                    text = str(item.get("text", "") or "").strip()
                    if not text or text.casefold() not in requested_lop_keys:
                        continue
                    current = record_map.setdefault(text.casefold(), {"text": text})
                    for field in ("text", "value", "khoi", "cap", "source", "weeks"):
                        if not current.get(field) and item.get(field):
                            current[field] = copy.deepcopy(item.get(field))
            else:
                q.put((
                    "log",
                    "Service metadata lớp không khả dụng; sẽ dùng fallback UI khi cần: "
                    f"{discover_payload}",
                    "warning",
                ))

            for class_index, lop_text in enumerate(lop_list, start=1):
                class_record = copy.deepcopy(record_map.get(lop_text.casefold()) or {"text": lop_text})
                target_weeks = []
                for week_num in week_numbers:
                    week_classes = classes_by_week.get(str(week_num))
                    if week_classes is not None and lop_text.casefold() not in {
                        str(item or "").strip().casefold() for item in list(week_classes or [])
                    }:
                        skipped_unavailable += 1
                        continue
                    target_weeks.append(week_num)
                if not target_weeks:
                    continue

                q.put((
                    "stats_status",
                    f"📝 KHBD | Service lớp {lop_text} ({class_index}/{len(lop_list)}) | "
                    f"{len(target_weeks)} tuần...",
                ))
                ok_bulk, bulk_payload = bridge.fetch_sodaubai_rows_bulk(
                    lop_text,
                    target_weeks,
                    timeout_s=12.0,
                    concurrency=6,
                    class_meta=class_record,
                    show_goi_y=True,
                )
                bulk_results = {}
                if ok_bulk:
                    bulk_results = {
                        int(item.get("requested_week", 0) or 0): item
                        for item in list(bulk_payload.get("results") or [])
                    }

                for week_num in target_weeks:
                    item = bulk_results.get(week_num) if ok_bulk else None
                    if item and item.get("ok"):
                        pairs_scanned += 1
                        pending_rows = _normalize_pending_rows(
                            list((item.get("payload") or {}).get("rows") or []),
                            True,
                        )
                        _append_report(week_num, lop_text, pending_rows)
                        continue

                    service_error = (
                        str(item.get("error", "") or "")
                        if item
                        else str(bulk_payload)
                    )
                    q.put((
                        "stats_status",
                        f"📝 KHBD | Fallback UI Tuần {week_num} | Lớp {lop_text}...",
                    ))
                    ok_ui, pending_rows, ui_error, unavailable = _scan_ui_fallback(
                        week_num,
                        lop_text,
                    )
                    if _fatal_if_cdp_closed(ui_error):
                        return
                    if unavailable:
                        skipped_unavailable += 1
                        continue
                    if not ok_ui:
                        issues.append({
                            "week": week_num,
                            "lop": lop_text,
                            "message": (
                                f"Service: {service_error or 'không có kết quả'} | "
                                f"Fallback UI: {ui_error}"
                            ),
                        })
                        continue
                    pairs_scanned += 1
                    _append_report(week_num, lop_text, pending_rows)

            q.put(("khdh_pending_done", {
                "tuan_from": int(params["tuan_from"]),
                "tuan_to": int(params["tuan_to"]),
                "lop_count": len(lop_list),
                "pairs_scanned": pairs_scanned,
                "pairs_with_pending": pairs_with_pending,
                "total_rows": total_rows,
                "skipped_unavailable": skipped_unavailable,
                "reports": reports,
                "issues": issues,
            }))
        except Exception as e:
            q.put(("stats_error", f"Worker quét row đỏ KHBD lỗi: {type(e).__name__}: {str(e)[:160]}"))
        finally:
            if bridge:
                try:
                    ok_cleanup, msg_cleanup = bridge.cleanup_after_automation(restore_view_mode=True)
                    q.put((
                        "log",
                        f"Cleanup sau KHDH: {msg_cleanup}" if ok_cleanup else f"Cleanup sau KHDH lỗi: {msg_cleanup}",
                        "info" if ok_cleanup else "warning",
                    ))
                except Exception:
                    pass
                try:
                    bridge.disconnect()
                except Exception:
                    pass

    def _collect_class_stats_report(self, bridge, lop_text, tuan_from, tuan_to,
                                    target_mon_hoc, status_cb=None, class_meta=None):
        """Thu thập report thống kê cho một lớp, dùng chung cho scan 1 lớp và all-class."""
        occurrences_by_ppct = {}
        invalid_ppct_rows = []
        week_errors = []
        latest_occurrence = None
        max_ppct = None
        missing_ppcts = []
        total_rows_with_data = 0
        weeks_scanned = 0
        cache_hits = 0

        for tuan_num in range(tuan_from, tuan_to + 1):
            tuan_text = f"Tuần {tuan_num}"
            if status_cb is not None:
                status_cb(f"⏳ Đang quét {lop_text} — {tuan_text}...")

            ok, payload, meta = self._fetch_class_stats_week_payload(
                bridge,
                lop_text,
                tuan_num,
                class_meta=class_meta,
            )
            weeks_scanned += 1
            if meta.get("from_cache"):
                cache_hits += 1
            if not ok:
                week_errors.append({"week": tuan_num, "message": str(payload)})
                continue

            table_rows = list(payload.get("rows") or [])
            occurrences, invalid_rows, matched_count = self._extract_class_stats_subject_rows(
                table_rows,
                tuan_num,
                target_mon_hoc,
            )
            invalid_ppct_rows.extend(invalid_rows)
            total_rows_with_data += matched_count

            for occurrence in occurrences:
                ppct_value = int(occurrence["ppct"])
                latest_occurrence = occurrence
                max_ppct = ppct_value if max_ppct is None else max(max_ppct, ppct_value)
                occurrences_by_ppct.setdefault(ppct_value, []).append(occurrence)

        duplicate_groups = []
        duplicate_weeks = set()
        unique_ppcts = sorted(occurrences_by_ppct.keys())
        if unique_ppcts:
            start_ppct = unique_ppcts[0]
            end_ppct = unique_ppcts[-1]
            existing_set = set(unique_ppcts)
            missing_ppcts = [
                ppct_value
                for ppct_value in range(start_ppct, end_ppct + 1)
                if ppct_value not in existing_set
            ]

        for ppct_value, items in sorted(occurrences_by_ppct.items()):
            if len(items) <= 1:
                continue
            weeks = sorted({int(item["week"]) for item in items})
            duplicate_weeks.update(weeks)
            duplicate_groups.append({
                "mon_hoc": items[0].get("mon_hoc", "(Không rõ môn)"),
                "ppct": ppct_value,
                "weeks": weeks,
                "items": items,
            })

        max_occurrences = list(occurrences_by_ppct.get(max_ppct, [])) if max_ppct is not None else []
        report = {
            "lop": lop_text,
            "mon_hoc": target_mon_hoc,
            "tuan_from": tuan_from,
            "tuan_to": tuan_to,
            "latest_occurrence": latest_occurrence,
            "max_ppct": max_ppct,
            "max_occurrences": max_occurrences,
            "missing_ppcts": missing_ppcts,
            "total_rows_with_data": total_rows_with_data,
            "duplicate_groups": duplicate_groups,
            "duplicate_weeks": sorted(duplicate_weeks),
            "invalid_ppct_rows": invalid_ppct_rows,
            "week_errors": week_errors,
            "scan_mode": "full",
            "weeks_scanned": weeks_scanned,
            "cache_hits": cache_hits,
        }
        return report

    def _collect_class_stats_fast_max_report(self, bridge, lop_text, tuan_from, tuan_to,
                                             target_mon_hoc, status_cb=None, class_meta=None):
        """Quét nhanh max PPCT bằng cách đi từ tuần cuối về đầu và dừng sớm."""
        week_errors = []
        total_rows_with_data = 0
        weeks_scanned = 0
        cache_hits = 0

        for tuan_num in range(tuan_to, tuan_from - 1, -1):
            tuan_text = f"Tuần {tuan_num}"
            if status_cb is not None:
                status_cb(f"⚡ Đang quét nhanh {lop_text} — {tuan_text}...")

            ok, payload, meta = self._fetch_class_stats_week_payload(
                bridge,
                lop_text,
                tuan_num,
                class_meta=class_meta,
            )
            weeks_scanned += 1
            if meta.get("from_cache"):
                cache_hits += 1
            if not ok:
                week_errors.append({"week": tuan_num, "message": str(payload)})
                continue

            table_rows = list(payload.get("rows") or [])
            occurrences, invalid_rows, matched_count = self._extract_class_stats_subject_rows(
                table_rows,
                tuan_num,
                target_mon_hoc,
            )
            total_rows_with_data += matched_count
            if not occurrences and not invalid_rows:
                continue

            max_ppct = None
            max_occurrences = []
            for occurrence in occurrences:
                ppct_value = int(occurrence["ppct"])
                if max_ppct is None or ppct_value > max_ppct:
                    max_ppct = ppct_value
                    max_occurrences = [occurrence]
                elif ppct_value == max_ppct:
                    max_occurrences.append(occurrence)

            latest_occurrence = max_occurrences[0] if max_occurrences else None
            return {
                "lop": lop_text,
                "mon_hoc": target_mon_hoc,
                "tuan_from": tuan_from,
                "tuan_to": tuan_to,
                "latest_occurrence": latest_occurrence,
                "max_ppct": max_ppct,
                "max_occurrences": max_occurrences,
                "missing_ppcts": [],
                "total_rows_with_data": total_rows_with_data,
                "duplicate_groups": [],
                "duplicate_weeks": [],
                "invalid_ppct_rows": invalid_rows,
                "week_errors": week_errors,
                "scan_mode": "fast_max",
                "weeks_scanned": weeks_scanned,
                "cache_hits": cache_hits,
                "stopped_early": True,
            }

        return {
            "lop": lop_text,
            "mon_hoc": target_mon_hoc,
            "tuan_from": tuan_from,
            "tuan_to": tuan_to,
            "latest_occurrence": None,
            "max_ppct": None,
            "max_occurrences": [],
            "missing_ppcts": [],
            "total_rows_with_data": total_rows_with_data,
            "duplicate_groups": [],
            "duplicate_weeks": [],
            "invalid_ppct_rows": [],
            "week_errors": week_errors,
            "scan_mode": "fast_max",
            "weeks_scanned": weeks_scanned,
            "cache_hits": cache_hits,
            "stopped_early": False,
        }

    def _class_stats_worker(self, params):
        """Worker đọc dữ liệu theo tuần/lớp và phát hiện trùng PPCT."""
        q = self._class_stats_queue
        bridge = None
        try:
            bridge = ChromeBridge(port=params["port"])
            ok, msg = bridge.connect()
            if not ok:
                q.put(("stats_error", f"Kết nối CDP thất bại: {msg}"))
                return

            report = self._collect_class_stats_report(
                bridge,
                lop_text=params["lop"],
                tuan_from=int(params["tuan_from"]),
                tuan_to=int(params["tuan_to"]),
                target_mon_hoc=params["mon_hoc"],
                status_cb=lambda text: q.put(("stats_status", text)),
                class_meta=copy.deepcopy(params.get("class_meta") or {}),
            )
            q.put(("stats_done", report))
        except Exception as e:
            q.put(("stats_error", f"Worker thống kê lỗi: {type(e).__name__}: {str(e)[:140]}"))
        finally:
            if bridge:
                try:
                    bridge.disconnect()
                except Exception:
                    pass

    def _class_stats_fast_overview_task(self, params, lop_text):
        """Task quét nhanh max PPCT cho một lớp, dùng trong overview nhiều lớp."""
        bridge = None
        class_meta = next((
            copy.deepcopy(item)
            for item in list(params.get("class_records") or [])
            if str(item.get("text", "") or "").strip().casefold()
            == str(lop_text).strip().casefold()
        ), {})
        try:
            bridge = ChromeBridge(port=params["port"])
            ok, msg = bridge.connect()
            if not ok:
                return {
                    "lop": lop_text,
                    "mon_hoc": params["mon_hoc"],
                    "tuan_from": int(params["tuan_from"]),
                    "tuan_to": int(params["tuan_to"]),
                    "latest_occurrence": None,
                    "max_ppct": None,
                    "max_occurrences": [],
                    "missing_ppcts": [],
                    "total_rows_with_data": 0,
                    "duplicate_groups": [],
                    "duplicate_weeks": [],
                    "invalid_ppct_rows": [],
                    "week_errors": [{
                        "week": int(params["tuan_to"]),
                        "message": f"Kết nối CDP thất bại: {msg}",
                    }],
                    "scan_mode": "fast_max",
                    "weeks_scanned": 0,
                    "cache_hits": 0,
                    "stopped_early": False,
                }

            return self._collect_class_stats_fast_max_report(
                bridge,
                lop_text=lop_text,
                tuan_from=int(params["tuan_from"]),
                tuan_to=int(params["tuan_to"]),
                target_mon_hoc=params["mon_hoc"],
                status_cb=None,
                class_meta=class_meta,
            )
        except Exception as e:
            return {
                "lop": lop_text,
                "mon_hoc": params["mon_hoc"],
                "tuan_from": int(params["tuan_from"]),
                "tuan_to": int(params["tuan_to"]),
                "latest_occurrence": None,
                "max_ppct": None,
                "max_occurrences": [],
                "missing_ppcts": [],
                "total_rows_with_data": 0,
                "duplicate_groups": [],
                "duplicate_weeks": [],
                "invalid_ppct_rows": [],
                "week_errors": [{
                    "week": int(params["tuan_to"]),
                    "message": f"Worker overview lỗi: {type(e).__name__}: {str(e)[:140]}",
                }],
                "scan_mode": "fast_max",
                "weeks_scanned": 0,
                "cache_hits": 0,
                "stopped_early": False,
            }
        finally:
            if bridge:
                try:
                    bridge.disconnect()
                except Exception:
                    pass

    def _class_stats_full_overview_task(self, params, lop_text):
        """Task quét FULL cho một lớp để phát hiện thiếu/trùng PPCT."""
        q = self._class_stats_queue
        bridge = None
        class_meta = next((
            copy.deepcopy(item)
            for item in list(params.get("class_records") or [])
            if str(item.get("text", "") or "").strip().casefold()
            == str(lop_text).strip().casefold()
        ), {})
        try:
            bridge = ChromeBridge(port=params["port"])
            ok, msg = bridge.connect()
            if not ok:
                return {
                    "lop": lop_text,
                    "mon_hoc": params["mon_hoc"],
                    "tuan_from": int(params["tuan_from"]),
                    "tuan_to": int(params["tuan_to"]),
                    "latest_occurrence": None,
                    "max_ppct": None,
                    "max_occurrences": [],
                    "missing_ppcts": [],
                    "total_rows_with_data": 0,
                    "duplicate_groups": [],
                    "duplicate_weeks": [],
                    "invalid_ppct_rows": [],
                    "week_errors": [{
                        "week": int(params["tuan_to"]),
                        "message": f"Kết nối CDP thất bại: {msg}",
                    }],
                    "scan_mode": "full",
                    "weeks_scanned": 0,
                    "cache_hits": 0,
                }

            return self._collect_class_stats_report(
                bridge,
                lop_text=lop_text,
                tuan_from=int(params["tuan_from"]),
                tuan_to=int(params["tuan_to"]),
                target_mon_hoc=params["mon_hoc"],
                status_cb=None,
                class_meta=class_meta,
            )
        except Exception as e:
            return {
                "lop": lop_text,
                "mon_hoc": params["mon_hoc"],
                "tuan_from": int(params["tuan_from"]),
                "tuan_to": int(params["tuan_to"]),
                "latest_occurrence": None,
                "max_ppct": None,
                "max_occurrences": [],
                "missing_ppcts": [],
                "total_rows_with_data": 0,
                "duplicate_groups": [],
                "duplicate_weeks": [],
                "invalid_ppct_rows": [],
                "week_errors": [{
                    "week": int(params["tuan_to"]),
                    "message": f"Worker full overview lỗi: {type(e).__name__}: {str(e)[:140]}",
                }],
                "scan_mode": "full",
                "weeks_scanned": 0,
                "cache_hits": 0,
            }
        finally:
            if bridge:
                try:
                    ok_cleanup, msg_cleanup = bridge.cleanup_after_automation(restore_view_mode=False)
                    q.put(("stats_status", f"Đã cleanup trạng thái web sau thống kê: {msg_cleanup}" if ok_cleanup else msg_cleanup))
                except Exception:
                    pass
                try:
                    bridge.disconnect()
                except Exception:
                    pass

    def _class_stats_all_worker(self, params):
        """Worker tổng hợp nhanh PPCT cao nhất cho toàn bộ lớp."""
        q = self._class_stats_queue
        reports = []
        try:
            lop_list = list(params.get("lop_list") or [])
            total = len(lop_list)
            scan_mode = str(params.get("scan_mode", "fast_max") or "fast_max")
            full_scan = scan_mode == "full_missing"
            if not lop_list:
                q.put(("stats_error", "Không có lớp nào để tổng hợp."))
                return

            max_workers = min(2 if full_scan else 3, max(1, total))
            mode_label = "FULL thiếu PPCT" if full_scan else "quét nhanh"
            q.put((
                "stats_status",
                f"{'🔎' if full_scan else '⚡'} Đang {mode_label} {total} lớp | "
                f"Môn {params['mon_hoc']} | {max_workers} luồng...",
            ))
            completed = 0
            task = self._class_stats_full_overview_task if full_scan else self._class_stats_fast_overview_task
            with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="classstats") as executor:
                future_map = {
                    executor.submit(task, params, lop_text): lop_text
                    for lop_text in lop_list
                }
                for future in as_completed(future_map):
                    lop_text = future_map[future]
                    report = future.result()
                    reports.append(report)
                    completed += 1
                    max_ppct = report.get("max_ppct")
                    max_text = str(max_ppct) if max_ppct is not None else "--"
                    missing_count = len(report.get("missing_ppcts") or [])
                    q.put((
                        "stats_status",
                        f"{'🔎' if full_scan else '⚡'} Đã xong {completed}/{total}: "
                        f"{lop_text} | Max PPCT {max_text}"
                        + (f" | Thiếu {missing_count}" if full_scan else ""),
                    ))

            q.put(("stats_overview_done", {
                "mon_hoc": params["mon_hoc"],
                "tuan_from": int(params["tuan_from"]),
                "tuan_to": int(params["tuan_to"]),
                "reports": reports,
                "scan_mode": "full" if full_scan else "fast_max",
                "max_workers": max_workers,
                "weeks_scanned": sum(int(r.get("weeks_scanned", 0) or 0) for r in reports),
                "cache_hits": sum(int(r.get("cache_hits", 0) or 0) for r in reports),
            }))
        except Exception as e:
            q.put(("stats_error", f"Worker tổng hợp lớp lỗi: {type(e).__name__}: {str(e)[:140]}"))

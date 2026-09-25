"""Hiển thị kết quả thống kê."""

import re


class ClassStatsRenderMixin:
    """Hiển thị kết quả thống kê."""

    def _render_class_stats_report(self, report):
        """Render kết quả thống kê lớp vào text area."""
        if not self._class_stats_result_text or not self._class_stats_result_text.winfo_exists():
            return

        txt = self._class_stats_result_text
        txt.config(state="normal")
        txt.delete("1.0", "end")

        lop_text = report.get("lop", "?")
        mon_hoc_text = report.get("mon_hoc", "?")
        tuan_from = report.get("tuan_from", "?")
        tuan_to = report.get("tuan_to", "?")
        latest_occurrence = report.get("latest_occurrence")
        max_ppct = report.get("max_ppct")
        max_occurrences = report.get("max_occurrences", [])
        missing_ppcts = report.get("missing_ppcts", [])
        duplicate_groups = report.get("duplicate_groups", [])
        duplicate_weeks = report.get("duplicate_weeks", [])
        invalid_ppct_rows = report.get("invalid_ppct_rows", [])
        week_errors = report.get("week_errors", [])
        aliased_weeks = [
            item for item in week_errors
            if "Bảng trả về đúng dữ liệu của Tuần" in str(item.get("message", ""))
        ]
        real_week_errors = [
            item for item in week_errors
            if item not in aliased_weeks
        ]
        total_rows = report.get("total_rows_with_data", 0)

        txt.insert("end", f"Thống kê lớp {lop_text} | Môn {mon_hoc_text}\n", "title")
        txt.insert("end", f"Phạm vi quét: Tuần {tuan_from} → Tuần {tuan_to}\n")
        txt.insert("end", f"Tổng số tiết đã có dữ liệu: {total_rows}\n")

        if latest_occurrence:
            txt.insert(
                "end",
                "PPCT gần nhất: "
                f"{latest_occurrence.get('ppct')} | {latest_occurrence.get('week_text')} | "
                f"{latest_occurrence.get('slot_label')} | {latest_occurrence.get('mon_hoc', '')}\n",
                "ok",
            )
        else:
            txt.insert("end", "PPCT gần nhất: chưa tìm thấy dữ liệu hợp lệ cho môn đã chọn\n", "warn")

        if max_ppct is not None:
            txt.insert("end", f"PPCT cao nhất đã thấy: {max_ppct}\n", "ok")
            if max_occurrences:
                first_max = max_occurrences[0]
                txt.insert(
                    "end",
                    f"PPCT cao nhất xuất hiện tại: {first_max.get('week_text')} | "
                    f"{first_max.get('slot_label')}\n",
                    "ok",
                )
        else:
            txt.insert("end", "PPCT cao nhất đã thấy: chưa có\n", "warn")

        if missing_ppcts:
            display_list = ", ".join(str(x) for x in missing_ppcts[:30])
            if len(missing_ppcts) > 30:
                display_list += f"... (+{len(missing_ppcts) - 30})"
            txt.insert("end", f"PPCT bị thiếu trong dải hiện có: {display_list}\n", "warn")
        else:
            txt.insert("end", "PPCT bị thiếu trong dải hiện có: không phát hiện\n", "ok")

        txt.insert("end", "\n")

        if duplicate_groups:
            txt.insert(
                "end",
                "CẢNH BÁO TRÙNG TIẾT PPCT CÙNG MÔN\n",
                "error",
            )
            txt.insert(
                "end",
                "Các tuần bị trùng: " + ", ".join(str(x) for x in duplicate_weeks) + "\n",
                "error",
            )
            for group in duplicate_groups:
                txt.insert(
                    "end",
                    f"- {group.get('mon_hoc', '(Không rõ môn)')} | PPCT {group['ppct']} "
                    f"trùng ở tuần {', '.join(str(x) for x in group['weeks'])}\n",
                    "error",
                )
                for item in group["items"]:
                    txt.insert(
                        "end",
                        f"    {item['week_text']} | {item['slot_label']} | {item.get('mon_hoc', '')}\n",
                        "error",
                    )
        else:
            txt.insert("end", "Không phát hiện PPCT trùng trong khoảng tuần đã quét.\n", "ok")

        txt.insert("end", "\n")

        if invalid_ppct_rows:
            txt.insert("end", "Tiết có dữ liệu nhưng PPCT bất thường\n", "warn")
            for item in invalid_ppct_rows:
                txt.insert(
                    "end",
                    f"- {item['week_text']} | {item['slot_label']} | {item.get('mon_hoc', '')} | {item['message']}\n",
                    "warn",
                )
            txt.insert("end", "\n")

        if aliased_weeks:
            txt.insert("end", "Tuần trả dữ liệu cũ / alias\n", "warn")
            for item in aliased_weeks:
                txt.insert(
                    "end",
                    f"- Tuần {item['week']}: {item['message']}\n",
                    "warn",
                )
            txt.insert("end", "\n")

        if real_week_errors:
            txt.insert("end", "Tuần quét lỗi / không đọc được\n", "error")
            for item in real_week_errors:
                txt.insert(
                    "end",
                    f"- Tuần {item['week']}: {item['message']}\n",
                    "error",
                )
            txt.insert("end", "\n")

        txt.config(state="disabled")

    def _render_class_stats_overview(self, payload):
        """Render bảng tổng hợp PPCT cao nhất cho toàn bộ lớp."""
        if not self._class_stats_result_text or not self._class_stats_result_text.winfo_exists():
            return

        txt = self._class_stats_result_text
        txt.config(state="normal")
        txt.delete("1.0", "end")

        mon_hoc_text = payload.get("mon_hoc", "?")
        tuan_from = payload.get("tuan_from", "?")
        tuan_to = payload.get("tuan_to", "?")
        reports = list(payload.get("reports") or [])
        scan_mode = str(payload.get("scan_mode", "fast_max") or "fast_max").strip()
        weeks_scanned = int(payload.get("weeks_scanned", 0) or 0)
        cache_hits = int(payload.get("cache_hits", 0) or 0)
        max_workers = int(payload.get("max_workers", 1) or 1)

        txt.insert("end", f"Tổng hợp PPCT cao nhất các lớp | Môn {mon_hoc_text}\n", "title")
        txt.insert("end", f"Phạm vi quét: Tuần {tuan_from} → Tuần {tuan_to}\n")
        txt.insert(
            "end",
            f"Số lớp đã tổng hợp: {len(reports)} | Chế độ: {scan_mode} | "
            f"Tuần đã quét thực tế: {weeks_scanned} | Cache hit: {cache_hits} | "
            f"Luồng: {max_workers}\n\n",
        )
        txt.insert(
            "end",
            "Lớp   | Max PPCT | Tuần      | Slot cao nhất         | Mode | Alias | Trùng | Thiếu\n",
            "title",
        )
        txt.insert("end", "-" * 96 + "\n", "muted")

        for report in sorted(reports, key=lambda item: str(item.get("lop", ""))):
            lop_text = str(report.get("lop", "") or "?")
            max_ppct = report.get("max_ppct")
            max_occurrences = list(report.get("max_occurrences") or [])
            missing_ppcts = list(report.get("missing_ppcts") or [])
            duplicate_groups = list(report.get("duplicate_groups") or [])
            week_errors = list(report.get("week_errors") or [])
            report_mode = str(report.get("scan_mode", "fast_max") or "fast_max").strip()
            alias_count = sum(
                1
                for item in week_errors
                if "Bảng trả về đúng dữ liệu của Tuần" in str(item.get("message", ""))
            )

            if max_occurrences:
                top_item = max_occurrences[0]
                week_text = str(top_item.get("week_text", "--"))
                slot_text = str(top_item.get("slot_label", "--"))
            else:
                week_text = "--"
                slot_text = "--"

            max_text = str(max_ppct) if max_ppct is not None else "--"
            duplicate_text = str(len(duplicate_groups)) if report_mode == "full" else "--"
            missing_text = str(len(missing_ppcts)) if report_mode == "full" else "--"
            mode_text = "FAST" if report_mode == "fast_max" else "FULL"
            line = (
                f"{lop_text:<5} | {max_text:>8} | {week_text:<9} | "
                f"{slot_text:<20} | {mode_text:<4} | {alias_count:>5} | {duplicate_text:>5} | "
                f"{missing_text:>5}\n"
            )
            tag = "ok"
            if max_ppct is None:
                tag = "muted"
            elif alias_count or (report_mode == "full" and (duplicate_groups or missing_ppcts)):
                tag = "warn"
            txt.insert("end", line, tag)

        txt.insert("end", "\n")
        txt.insert("end", "Cột 'Alias' = số tuần trả lại đúng dữ liệu của tuần khác.\n", "muted")
        if scan_mode == "full":
            txt.insert("end", "\nChi tiết lớp thiếu/trùng PPCT\n", "title")
            any_detail = False
            for report in sorted(reports, key=lambda item: str(item.get("lop", ""))):
                lop_text = str(report.get("lop", "") or "?")
                missing_ppcts = list(report.get("missing_ppcts") or [])
                duplicate_groups = list(report.get("duplicate_groups") or [])
                invalid_ppct_rows = list(report.get("invalid_ppct_rows") or [])
                if not (missing_ppcts or duplicate_groups or invalid_ppct_rows):
                    continue
                any_detail = True
                txt.insert("end", f"- {lop_text}:\n", "warn")
                if missing_ppcts:
                    display_list = ", ".join(str(x) for x in missing_ppcts[:60])
                    if len(missing_ppcts) > 60:
                        display_list += f"... (+{len(missing_ppcts) - 60})"
                    txt.insert("end", f"    Thiếu PPCT: {display_list}\n", "warn")
                if duplicate_groups:
                    duplicate_text = ", ".join(
                        f"{group.get('ppct')} (tuần {', '.join(str(x) for x in group.get('weeks', []))})"
                        for group in duplicate_groups[:20]
                    )
                    if len(duplicate_groups) > 20:
                        duplicate_text += f"... (+{len(duplicate_groups) - 20})"
                    txt.insert("end", f"    Trùng PPCT: {duplicate_text}\n", "error")
                if invalid_ppct_rows:
                    txt.insert(
                        "end",
                        f"    PPCT bất thường: {len(invalid_ppct_rows)} tiết có dữ liệu nhưng PPCT trống/không hợp lệ\n",
                        "warn",
                    )
            if not any_detail:
                txt.insert("end", "Không phát hiện lớp thiếu/trùng PPCT trong khoảng tuần đã quét.\n", "ok")
        else:
            txt.insert("end", "Mode FAST = quét từ tuần cuối về đầu và dừng ở tuần gần nhất có dữ liệu môn.\n", "muted")
            txt.insert("end", "Ở Mode FAST, cột 'Trùng' và 'Thiếu' hiển thị '--' vì chưa quét đầy đủ.\n", "muted")
        txt.config(state="disabled")

    def _render_missing_teacher_audit(self, payload):
        """Render kết quả quét GV chưa nhập từ màn thống kê."""
        if not self._class_stats_result_text or not self._class_stats_result_text.winfo_exists():
            return

        txt = self._class_stats_result_text
        txt.config(state="normal")
        txt.delete("1.0", "end")

        tuan_from = int(payload.get("tuan_from", 0) or 0)
        tuan_to = int(payload.get("tuan_to", 0) or 0)
        reports = list(payload.get("reports") or [])
        issues = list(payload.get("issues") or [])
        pairs_scanned = int(payload.get("pairs_scanned", 0) or 0)
        pairs_with_missing = int(payload.get("pairs_with_missing", 0) or 0)
        class_count = int(payload.get("class_count", 0) or 0)
        teacher_rows = sum(len(item.get("teachers") or []) for item in reports)

        txt.insert("end", "GV chưa nhập theo màn thống kê VnEdu\n", "title")
        txt.insert("end", f"Phạm vi quét: Tuần {tuan_from} → Tuần {tuan_to}\n")
        txt.insert(
            "end",
            f"Lớp đã quét: {class_count} | Cặp tuần/lớp đã đọc: {pairs_scanned} | "
            f"Cặp có thiếu: {pairs_with_missing} | Dòng giáo viên thiếu: {teacher_rows}\n\n",
        )

        if not reports:
            txt.insert("end", "Không phát hiện giáo viên nào chưa nhập trong phạm vi đã quét.\n", "ok")
        else:
            for report in reports:
                txt.insert(
                    "end",
                    f"{report.get('week_text', 'Tuần ?')} | Lớp {report.get('lop', '?')}\n",
                    "section",
                )
                for teacher in list(report.get("teachers") or []):
                    teacher_name = str(teacher.get("teacher_name", "") or "").strip() or "(Không rõ giáo viên)"
                    subject_text = str(teacher.get("mon_hoc", "") or "").strip() or "(Không rõ môn)"
                    total_missing = int(teacher.get("total_missing", 0) or 0)
                    txt.insert(
                        "end",
                        f"- {teacher_name} | {subject_text} | Tổng thiếu {total_missing}\n",
                        "title",
                    )

                    exact_slots = list(teacher.get("exact_slots") or [])
                    ambiguous_buckets = list(teacher.get("ambiguous_buckets") or [])
                    unmatched_buckets = list(teacher.get("unmatched_buckets") or [])

                    if exact_slots:
                        for slot in exact_slots:
                            txt.insert(
                                "end",
                                f"    ✓ {slot.get('slot_label', '--')}\n",
                                "ok",
                            )
                    if ambiguous_buckets:
                        for bucket in ambiguous_buckets:
                            candidates = ", ".join(
                                slot.get("slot_label", "--")
                                for slot in list(bucket.get("candidate_slots") or [])
                            ) or "không có"
                            txt.insert(
                                "end",
                                f"    ? {bucket.get('buoi', '?')} Thứ {bucket.get('thu', '?')} | "
                                f"Thiếu {bucket.get('count', '?')} | Ứng viên: {candidates}\n",
                                "warn",
                            )
                    if unmatched_buckets:
                        for bucket in unmatched_buckets:
                            txt.insert(
                                "end",
                                f"    ! {bucket.get('buoi', '?')} Thứ {bucket.get('thu', '?')} | "
                                f"Thiếu {bucket.get('count', '?')} | {bucket.get('note', '')}\n",
                                "error",
                            )
                    if not (exact_slots or ambiguous_buckets or unmatched_buckets):
                        txt.insert(
                            "end",
                            "    Không suy ra được bucket/tiết cụ thể từ dữ liệu hiện có.\n",
                            "warn",
                        )
                txt.insert("end", "\n")

        if issues:
            txt.insert("end", "Các cặp tuần/lớp đọc lỗi hoặc thiếu dữ liệu\n", "section")
            for item in issues[:120]:
                lop_text = str(item.get("lop", "") or "").strip()
                prefix = f"Tuần {item.get('week', '?')}"
                if lop_text:
                    prefix += f" | {lop_text}"
                txt.insert("end", f"- {prefix}: {item.get('message', '')}\n", "error")
            if len(issues) > 120:
                txt.insert("end", f"... còn {len(issues) - 120} lỗi khác\n", "muted")

        txt.config(state="disabled")

    def _render_khdh_pending_audit(self, payload):
        """Render kết quả quét row đỏ KHBD/KHDH chưa lên."""
        if not self._class_stats_result_text or not self._class_stats_result_text.winfo_exists():
            return

        txt = self._class_stats_result_text
        txt.config(state="normal")
        txt.delete("1.0", "end")

        tuan_from = int(payload.get("tuan_from", 0) or 0)
        tuan_to = int(payload.get("tuan_to", 0) or 0)
        lop_count = int(payload.get("lop_count", 0) or 0)
        pairs_scanned = int(payload.get("pairs_scanned", 0) or 0)
        pairs_with_pending = int(payload.get("pairs_with_pending", 0) or 0)
        total_rows = int(payload.get("total_rows", 0) or 0)
        skipped_unavailable = int(payload.get("skipped_unavailable", 0) or 0)
        reports = list(payload.get("reports") or [])
        issues = list(payload.get("issues") or [])

        txt.insert("end", "Row đỏ KHBD/KHDH chưa lên lịch\n", "title")
        txt.insert("end", f"Phạm vi quét: Tuần {tuan_from} → Tuần {tuan_to}\n")
        txt.insert(
            "end",
            f"Lớp đã quét: {lop_count} | Cặp tuần/lớp đã đọc: {pairs_scanned} | "
            f"Cặp còn row đỏ: {pairs_with_pending} | Tổng row đỏ còn lại: {total_rows}\n\n",
        )
        if skipped_unavailable:
            txt.insert(
                "end",
                f"Đã bỏ qua {skipped_unavailable} cặp tuần/lớp không tồn tại trong tuần tương ứng.\n\n",
                "muted",
            )

        if not reports:
            txt.insert("end", "Không phát hiện row đỏ KHBD/KHDH nào còn nút + trong phạm vi đã quét.\n", "ok")
        else:
            for report in reports:
                txt.insert(
                    "end",
                    f"{report.get('week_text', 'Tuần ?')} | Lớp {report.get('lop', '?')} | "
                    f"{len(report.get('rows') or [])} row đỏ\n",
                    "section",
                )
                for item in list(report.get("rows") or []):
                    mon_hoc = str(item.get("mon_hoc_hint", "") or "").strip() or "(Không rõ môn)"
                    ppct_hint = str(item.get("ppct_hint", "") or "").strip() or "--"
                    noi_dung_hint = str(item.get("noi_dung_hint", "") or "").strip()
                    line = (
                        f"- {item.get('slot_label', '--')} | {mon_hoc} | PPCT {ppct_hint}"
                    )
                    if noi_dung_hint:
                        line += f" | {noi_dung_hint}"
                    txt.insert("end", line + "\n", "warn")
                txt.insert("end", "\n")

        if issues:
            txt.insert("end", "Các cặp tuần/lớp đọc lỗi\n", "section")
            for item in issues[:160]:
                txt.insert(
                    "end",
                    f"- Tuần {item.get('week', '?')} | {item.get('lop', '?')}: {item.get('message', '')}\n",
                    "error",
                )
            if len(issues) > 160:
                txt.insert("end", f"... còn {len(issues) - 160} lỗi khác\n", "muted")

        txt.config(state="disabled")

    def _poll_class_stats_queue(self):
        """Poll queue cho dialog thống kê lớp."""
        try:
            while not self._class_stats_queue.empty():
                msg = self._class_stats_queue.get_nowait()
                msg_type = msg[0]

                if msg_type == "options_ready":
                    self._class_stats_thread = None
                    payload = msg[1]
                    lop_options = [x for x in payload.get("lop_options", []) if x and not x.startswith("--")]
                    self._class_stats_lop_records = list(payload.get("lop_records", []) or [])
                    tuan_options = payload.get("tuan_options", [])
                    mon_hoc_options = payload.get("mon_hoc_options", [])
                    mon_hoc_note = str(payload.get("mon_hoc_note", "") or "").strip()
                    class_scan_scope = str(payload.get("class_scan_scope", "") or "").strip()
                    class_weeks_scanned = int(payload.get("class_weeks_scanned", 0) or 0)
                    self._class_stats_tuan_options = list(tuan_options)
                    self._populate_class_stats_buttons(lop_options)
                    selected_subject = self._populate_class_stats_mon_hoc(mon_hoc_options)

                    nums = []
                    for item in tuan_options:
                        match = re.search(r"\d+", str(item))
                        if match:
                            nums.append(int(match.group()))
                    if nums:
                        self.var_stats_tuan_from.set(min(nums))
                        self.var_stats_tuan_to.set(max(nums))
                    scope_note = (
                        f" DS lớp quét {class_weeks_scanned} tuần ({class_scan_scope})."
                        if class_scan_scope else ""
                    )
                    self._set_class_stats_status(
                        f"Đã tải {len(lop_options)} lớp, {len(mon_hoc_options)} môn.{scope_note} "
                        f"Chọn môn '{selected_subject or '(chưa chọn)'}' để quét PPCT, "
                        f"hoặc dùng nút 'GV chưa nhập' để rà giáo viên thiếu theo tuần/lớp.",
                        "#1f7a1f",
                    )
                    self._log(
                        f"Đã tải {len(lop_options)} lớp, {len(mon_hoc_options)} môn cho thống kê",
                        "success",
                    )
                    if mon_hoc_note:
                        self._log(mon_hoc_note, "warning")

                elif msg_type == "options_error":
                    self._class_stats_thread = None
                    self._set_class_stats_status(msg[1], "#b00020")
                    self._log(msg[1], "error")

                elif msg_type == "stats_status":
                    self._set_class_stats_status(msg[1], "#a86400")

                elif msg_type == "log":
                    self._log(msg[1], msg[2] if len(msg) > 2 else "info")

                elif msg_type == "stats_done":
                    self._class_stats_thread = None
                    report = msg[1]
                    self._render_class_stats_report(report)
                    duplicate_groups = report.get("duplicate_groups", [])
                    duplicate_weeks = report.get("duplicate_weeks", [])
                    mon_hoc_text = report.get("mon_hoc", "?")
                    if duplicate_groups:
                        self._set_class_stats_status(
                            f"Lớp {report['lop']} | Môn {mon_hoc_text} có PPCT trùng ở tuần: "
                            + ", ".join(str(x) for x in duplicate_weeks),
                            "#b00020",
                        )
                        self._log(
                            f"⚠ Thống kê lớp {report['lop']} | Môn {mon_hoc_text}: phát hiện {len(duplicate_groups)} nhóm PPCT trùng",
                            "warning",
                        )
                    else:
                        self._set_class_stats_status(
                            f"Lớp {report['lop']} | Môn {mon_hoc_text}: không phát hiện PPCT trùng trong phạm vi quét.",
                            "#1f7a1f",
                        )
                        self._log(
                            f"📊 Thống kê lớp {report['lop']} | Môn {mon_hoc_text} hoàn tất: không có PPCT trùng",
                            "success",
                        )
                    self._set_class_stats_running(False)
                    return

                elif msg_type == "stats_overview_done":
                    self._class_stats_thread = None
                    payload = msg[1]
                    reports = list(payload.get("reports") or [])
                    self._render_class_stats_overview(payload)
                    weeks_scanned = int(payload.get("weeks_scanned", 0) or 0)
                    cache_hits = int(payload.get("cache_hits", 0) or 0)
                    self._set_class_stats_status(
                        f"Đã quét nhanh {len(reports)} lớp cho môn {payload.get('mon_hoc', '?')} "
                        f"| Tuần thực quét: {weeks_scanned} | Cache hit: {cache_hits}.",
                        "#1f7a1f",
                    )
                    self._log(
                        f"⚡ Đã tổng hợp nhanh PPCT cao nhất cho {len(reports)} lớp | "
                        f"Môn {payload.get('mon_hoc', '?')} | Tuần thực quét {weeks_scanned} | "
                        f"Cache hit {cache_hits}",
                        "success",
                    )
                    self._set_class_stats_running(False)
                    return

                elif msg_type == "missing_teacher_done":
                    self._class_stats_thread = None
                    payload = msg[1]
                    reports = list(payload.get("reports") or [])
                    pairs_with_missing = int(payload.get("pairs_with_missing", 0) or 0)
                    issues = list(payload.get("issues") or [])
                    self._render_missing_teacher_audit(payload)
                    self._set_class_stats_status(
                        f"Đã quét GV chưa nhập: {len(reports)} cặp tuần/lớp có thiếu | "
                        f"Lỗi đọc: {len(issues)} | Cặp có thiếu: {pairs_with_missing}.",
                        "#1f7a1f" if not issues else "#a86400",
                    )
                    self._log(
                        f"👤 Hoàn tất quét GV chưa nhập | Có thiếu: {len(reports)} cặp tuần/lớp | "
                        f"Lỗi: {len(issues)}",
                        "success" if not issues else "warning",
                    )
                    self._set_class_stats_running(False)
                    return

                elif msg_type == "khdh_pending_done":
                    self._class_stats_thread = None
                    payload = msg[1]
                    reports = list(payload.get("reports") or [])
                    issues = list(payload.get("issues") or [])
                    total_rows = int(payload.get("total_rows", 0) or 0)
                    skipped_unavailable = int(payload.get("skipped_unavailable", 0) or 0)
                    self._render_khdh_pending_audit(payload)
                    self._set_class_stats_status(
                        f"Đã quét row đỏ KHBD/KHDH: {len(reports)} cặp tuần/lớp còn việc | "
                        f"Tổng row đỏ: {total_rows} | Bỏ qua không có lớp: {skipped_unavailable} | Lỗi đọc: {len(issues)}.",
                        "#1f7a1f" if not issues else "#a86400",
                    )
                    self._log(
                        f"📝 Hoàn tất quét row đỏ KHBD/KHDH | Cặp còn việc: {len(reports)} | "
                        f"Row đỏ: {total_rows} | Bỏ qua không có lớp: {skipped_unavailable} | Lỗi: {len(issues)}",
                        "success" if not issues else "warning",
                    )
                    self._set_class_stats_running(False)
                    return

                elif msg_type == "stats_error":
                    self._class_stats_thread = None
                    self._set_class_stats_status(msg[1], "#b00020")
                    self._log(msg[1], "error")
                    self._set_class_stats_running(False)
                    return

        except Exception as e:
            self._set_class_stats_status(f"Lỗi poll thống kê: {e}", "#b00020")
            self._class_stats_thread = None
            self._set_class_stats_running(False)
            return

        if (
            (self._class_stats_running or (self._class_stats_thread and self._class_stats_thread.is_alive()))
            and self._root_exists()
        ):
            self.root.after(150, self._poll_class_stats_queue)

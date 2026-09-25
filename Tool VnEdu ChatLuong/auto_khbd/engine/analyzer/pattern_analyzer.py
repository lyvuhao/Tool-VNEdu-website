"""PatternAnalyzer: phân tích mẫu TKB cả năm và kiểm tra PPCT."""

from __future__ import annotations

from collections import Counter, defaultdict

from ..parser import WeekData
from ..profile.profile import KHDHProfile
from .models import (
    _SEVERITY_ORDER,
    _SlotKey,
    PATTERN_BOTH,
    PATTERN_CHAN_ONLY,
    PATTERN_LE_ONLY,
    PATTERN_NONE,
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    TT_BINH_THUONG,
    TT_COUNTED,
    TT_EXTRA,
    YearTKBPattern,
)
from .reports import (
    HEALTH_KIND_GROUP_NO_DATA,
    HEALTH_KIND_PPCT_DUPLICATE,
    HEALTH_KIND_PPCT_GAP,
    HEALTH_KIND_SLOT_EXTRA,
    HEALTH_KIND_SLOT_MISSING,
    HealthIssue,
    HealthScanResult,
    SubjectProgress,
    YearScanReport,
)


# ---------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------

class PatternAnalyzer:
    """Static analyzer — không giữ state."""

    @staticmethod
    def cluster_patterns(weeks_data: dict[int, WeekData]) -> list[YearTKBPattern]:
        """Cluster các tuần có cùng TKB pattern.

        Pattern = set các (thu, buoi, tiet, lop_id, mon_id) có lop ≠ ''/0.
        Bỏ qua tên bài, PPCT, phan_mon — chỉ so cấu trúc TKB.
        """
        sig_to_weeks: dict[frozenset[_SlotKey], list[int]] = defaultdict(list)
        for tuan, wd in weeks_data.items():
            sig = frozenset(
                _SlotKey(s.thu, s.buoi_idx, s.tiet_idx, s.lop_id, s.mon_id or s.mon_dmonid)
                for s in wd.slots
                if s.has_lop
            )
            sig_to_weeks[sig].append(tuan)
        patterns = []
        for i, (sig, weeks) in enumerate(sorted(sig_to_weeks.items(), key=lambda x: -len(x[1])), 1):
            patterns.append(YearTKBPattern(
                pattern_id=i,
                weeks=sorted(weeks),
                slots=sorted(sig, key=lambda k: (k.thu, k.buoi_idx, k.tiet_idx)),
            ))
        return patterns

    @staticmethod
    def compute_progress(weeks_data: dict[int, WeekData]) -> dict[str, SubjectProgress]:
        """Compute tiến độ PPCT theo (lop, mon, phan_mon).

        Duyệt từ tuần thấp → cao, ghi nhận PPCT cao nhất cho mỗi nhóm.
        Khi 2 slot cùng nhóm trong 1 tuần (3 lớp song song), chỉ cần lấy max.

        v2 (extras-aware):
          - Filter slot theo trang_thai: chỉ tính nếu trang_thai ∈ TT_COUNTED.
          - Slot rỗng/placeholder (trang_thai="") được tính như TT_BINH_THUONG
            để giữ backward-compat với data cũ chưa có cboTrangThai render.
          - Slot có trang_thai ∈ TT_EXTRA → tăng `extra_count` và lưu vào
            `extras` để executor/UI biết PPCT đã shift bao nhiêu vì có dạy bù.
        """
        progress: dict[str, SubjectProgress] = {}
        sorted_weeks = sorted(weeks_data.keys())
        for tuan in sorted_weeks:
            wd = weeks_data[tuan]
            for slot in wd.filled_slots:
                if not slot.lop_id or not slot.phan_mon_id:
                    continue
                # Skip slot không có ppct numeric hoặc ppct=0
                try:
                    ppct_int = int(slot.ppct)
                except (ValueError, TypeError):
                    continue
                if ppct_int <= 0:
                    continue

                # v2: Filter theo trạng thái — bỏ Nghỉ/Phụ đạo/Bồi dưỡng/Dạy thêm.
                # Slot không có trang_thai (display mode chưa render select) →
                # treat như "Bình thường" để không bỏ sót data cũ.
                tt = (slot.trang_thai or "").strip()
                if tt and tt not in TT_COUNTED:
                    continue

                key = f"{slot.lop_id}|{slot.mon_id}|{slot.phan_mon_id}"
                p = progress.get(key)
                if p is None:
                    p = SubjectProgress(
                        lop_id=slot.lop_id,
                        lop_text=slot.lop_text,
                        mon_id=slot.mon_id,
                        mon_text=slot.mon_text or "",
                        phan_mon_id=slot.phan_mon_id,
                        phan_mon_text=slot.phan_mon_text,
                    )
                    progress[key] = p
                else:
                    # Enrich nếu lần đầu thiếu
                    if not p.mon_text and slot.mon_text:
                        p.mon_text = slot.mon_text
                    if not p.phan_mon_text or p.phan_mon_text == p.phan_mon_id:
                        if slot.phan_mon_text and slot.phan_mon_text != slot.phan_mon_id:
                            p.phan_mon_text = slot.phan_mon_text

                p.ppct_history.append((tuan, ppct_int, slot.ten_bai))
                if ppct_int > p.last_ppct:
                    p.last_ppct = ppct_int
                    p.last_ten_bai = slot.ten_bai
                    p.last_tuan = tuan

                # v2: Track tiết "extra" (dạy bù / chèn lịch / dạy chung)
                if tt in TT_EXTRA:
                    p.extra_count += 1
                    p.extras.append((tuan, slot.thu, slot.buoi_idx,
                                    slot.tiet_idx, ppct_int, tt))

        # Compute next_ppct
        for p in progress.values():
            p.next_ppct = p.last_ppct + 1
            # Sort history by ppct
            p.ppct_history.sort(key=lambda x: (x[0], x[1]))
            # Sort extras by (tuan, thu, buoi, tiet)
            p.extras.sort(key=lambda x: (x[0], x[1], x[2], x[3]))
        return progress

    @staticmethod
    def build_report(weeks_data: dict[int, WeekData]) -> YearScanReport:
        """Build full scan report."""
        rpt = YearScanReport()
        rpt.weeks_scanned = sorted(weeks_data.keys())
        rpt.weeks_with_data = sorted([t for t, w in weeks_data.items() if w.filled_slots])
        rpt.weeks_empty = sorted([t for t, w in weeks_data.items() if not w.filled_slots])
        rpt.patterns = PatternAnalyzer.cluster_patterns(weeks_data)
        rpt.subject_progress = PatternAnalyzer.compute_progress(weeks_data)

        # Pull aPhanMon từ tuần đầu tiên (chung toàn trường)
        if weeks_data:
            for t in rpt.weeks_scanned:
                wd = weeks_data[t]
                if wd.a_phan_mon:
                    rpt.a_phan_mon_global = wd.a_phan_mon
                    break

        # Enrich mon_text + phan_mon_text từ aPhanMon
        if rpt.a_phan_mon_global:
            mon_text_map = {}
            phan_mon_text_map = {}
            for pm in rpt.a_phan_mon_global:
                mon_id = str(pm.get("mon_hoc_id", ""))
                ten_mon = pm.get("ten_mon", "")
                if mon_id and ten_mon and mon_id not in mon_text_map:
                    mon_text_map[mon_id] = ten_mon
                pm_id = str(pm.get("id", ""))
                pm_ten = pm.get("ten", "") or pm.get("sten", "")
                if pm_id and pm_ten and pm_id not in phan_mon_text_map:
                    phan_mon_text_map[pm_id] = pm_ten
            for p in rpt.subject_progress.values():
                if (not p.mon_text or p.mon_text == p.mon_id) and p.mon_id in mon_text_map:
                    p.mon_text = mon_text_map[p.mon_id]
                if (
                    not p.phan_mon_text
                    or p.phan_mon_text == p.phan_mon_id
                    or p.phan_mon_text.isdigit()
                ) and p.phan_mon_id in phan_mon_text_map:
                    p.phan_mon_text = phan_mon_text_map[p.phan_mon_id]
        return rpt

    @staticmethod
    def find_pattern_for_week(report: YearScanReport, tuan: int) -> YearTKBPattern | None:
        for p in report.patterns:
            if tuan in p.weeks:
                return p
        return None

    @staticmethod
    def detect_extra_slots(
        week_data: "WeekData",
        tkb_template_keys: set[tuple[int, int, int, str, str]] | None = None,
    ) -> dict[str, list[dict]]:
        """Phát hiện slot "extra" (tiết bù / chèn lịch / dạy chung) trong 1 tuần.

        Logic 2 tầng:
          1. Primary: slot có `trang_thai` ∈ TT_EXTRA → là extra (cao confidence).
          2. Heuristic: slot có lop+mon+pm + trang_thai ∈ {"", "0"} (Bình thường)
             NHƯNG (thu, buoi, tiet, lop_id, mon_id) không thuộc tkb_template_keys
             → cũng là extra (user chưa khai trạng thái Dạy bù đúng).

        Args:
            week_data: WeekData của 1 tuần.
            tkb_template_keys: set các tuple (thu, buoi, tiet, lop_id, mon_id)
                định nghĩa TKB chuẩn của tuần này (lấy từ TKBPattern.slots
                hoặc KHDHProfile.get_active_template). Nếu None → chỉ dùng tầng 1.

        Returns:
            dict[group_key, list[extra_info]]:
              group_key = "lop_id|mon_id|phan_mon_id"
              extra_info = {
                "thu": int, "buoi": int, "tiet": int,
                "ppct": int, "trang_thai": str, "ten_bai": str,
                "source": "trang_thai" | "heuristic"
              }
        """
        result: dict[str, list[dict]] = {}
        for slot in week_data.filled_slots:
            if not slot.lop_id or not slot.phan_mon_id:
                continue
            try:
                ppct_int = int(slot.ppct)
            except (ValueError, TypeError):
                continue
            if ppct_int <= 0:
                continue

            tt = (slot.trang_thai or "").strip()
            # Skip slot không tính PPCT (Nghỉ/Phụ đạo/Bồi dưỡng/Dạy thêm)
            if tt and tt not in TT_COUNTED:
                continue

            is_extra = False
            source = ""
            if tt in TT_EXTRA:
                is_extra = True
                source = "trang_thai"
            elif tkb_template_keys is not None and tt in ("", TT_BINH_THUONG):
                # Heuristic: slot có data nhưng không khớp TKB template
                key = (slot.thu, slot.buoi_idx, slot.tiet_idx,
                       slot.lop_id, slot.mon_id or slot.mon_dmonid)
                if key not in tkb_template_keys:
                    is_extra = True
                    source = "heuristic"

            if not is_extra:
                continue

            group_key = f"{slot.lop_id}|{slot.mon_id}|{slot.phan_mon_id}"
            result.setdefault(group_key, []).append({
                "thu": slot.thu,
                "buoi": slot.buoi_idx,
                "tiet": slot.tiet_idx,
                "ppct": ppct_int,
                "trang_thai": tt,
                "ten_bai": slot.ten_bai,
                "source": source,
            })
        return result

    @staticmethod
    def compute_subject_breakdown(
        weeks_data: dict[int, "WeekData"],
        progress: dict[str, "SubjectProgress"] | None = None,
    ) -> dict[str, dict]:
        """Tính breakdown theo lẻ/chẵn + gap PPCT cho mỗi nhóm.

        Args:
            weeks_data: dict {tuan: WeekData} đã parse.
            progress: KHÔNG cần thiết cho logic — kept for backward compat.
                Caller có thể pass `None` hoặc bỏ qua.

        Trả dict[group_key] = {
            "le_ppcts": sorted list[int],     # PPCT xuất hiện ở tuần lẻ
            "chan_ppcts": sorted list[int],   # PPCT xuất hiện ở tuần chẵn
            "le_weeks": list[int],            # Tuần lẻ có dữ liệu
            "chan_weeks": list[int],          # Tuần chẵn có dữ liệu
            "pattern": "le_only" | "chan_only" | "both" | "none",
            "gaps": list[int],                # Các PPCT thiếu trong dãy 1..max
        }

        Lưu ý: PPCT là tuyến tính theo môn (server đếm cộng dồn không phân
        biệt lẻ/chẵn). Breakdown này chỉ để báo cáo cho user xem rõ pattern.
        """
        del progress  # marked as dead — function chỉ cần weeks_data
        out: dict[str, dict] = {}
        for tuan, wd in weeks_data.items():
            is_chan = KHDHProfile.is_chan(int(tuan))
            for slot in wd.filled_slots:
                if not slot.lop_id or not slot.phan_mon_id:
                    continue
                try:
                    ppct = int(slot.ppct)
                except (ValueError, TypeError):
                    continue
                if ppct <= 0:
                    continue
                key = f"{slot.lop_id}|{slot.mon_id}|{slot.phan_mon_id}"
                bd = out.setdefault(key, {
                    "le_ppcts": set(), "chan_ppcts": set(),
                    "le_weeks": set(), "chan_weeks": set(),
                })
                if is_chan:
                    bd["chan_ppcts"].add(ppct)
                    bd["chan_weeks"].add(int(tuan))
                else:
                    bd["le_ppcts"].add(ppct)
                    bd["le_weeks"].add(int(tuan))
        # Convert sets → sorted lists + tính pattern + gap
        for key, bd in out.items():
            le_p = sorted(bd["le_ppcts"])
            chan_p = sorted(bd["chan_ppcts"])
            le_w = sorted(bd["le_weeks"])
            chan_w = sorted(bd["chan_weeks"])
            bd["le_ppcts"] = le_p
            bd["chan_ppcts"] = chan_p
            bd["le_weeks"] = le_w
            bd["chan_weeks"] = chan_w
            if le_p and chan_p:
                bd["pattern"] = PATTERN_BOTH
            elif le_p and not chan_p:
                bd["pattern"] = PATTERN_LE_ONLY
            elif chan_p and not le_p:
                bd["pattern"] = PATTERN_CHAN_ONLY
            else:
                bd["pattern"] = PATTERN_NONE
            # Tính gap = các PPCT thiếu trong dãy 1..max
            all_p = sorted(set(le_p) | set(chan_p))
            if all_p:
                full = set(range(1, max(all_p) + 1))
                gaps = sorted(full - set(all_p))
                bd["gaps"] = gaps
            else:
                bd["gaps"] = []
        return out

    @staticmethod
    def compute_health_warnings(
        weeks_data: dict[int, "WeekData"],
        report: "YearScanReport",
        profile=None,
        breakdown: dict[str, dict] | None = None,
    ) -> list[dict]:
        """Phân tích sức khỏe data web — phát hiện vấn đề tiềm ẩn.

        Args:
            weeks_data: dict {tuan: WeekData}.
            report: YearScanReport đã build.
            profile: KHDHProfile (tùy chọn) — để so sánh missing/orphan.
            breakdown: kết quả của `compute_subject_breakdown` nếu caller đã
                tính sẵn — tránh tính lại lần 2 (perf optimization).

        Trả list[dict]:
            {type, severity: "info"|"warning"|"error", title, message, details}

        Các loại cảnh báo:
        1. weeks_missing — Tuần giữa năm không có data (gap ở weeks_with_data)
        2. ppct_gap — Group có PPCT bị thiếu ở giữa
        3. pattern_inconsistent — Pattern lẻ/chẵn xen kẽ bất thường
        4. group_orphan_in_profile — Group trên web không có trong profile
        5. group_missing_on_web — Group trong profile không thấy trên web
        6. ppct_duplicate — Cùng group có 2+ slot cùng PPCT trong 1 tuần (lỗi)
        """
        warnings: list[dict] = []

        # 1. Tuần giữa năm không có data
        scanned = sorted(report.weeks_scanned or [])
        with_data = set(report.weeks_with_data or [])
        if scanned and with_data:
            min_w_data = min(with_data)
            max_w_data = max(with_data)
            missing = [
                t for t in range(min_w_data, max_w_data + 1)
                if t in set(scanned) and t not in with_data
            ]
            if missing:
                warnings.append({
                    "type": "weeks_missing",
                    "severity": SEVERITY_WARNING,
                    "title": f"Có {len(missing)} tuần giữa năm bị trống",
                    "message": (
                        f"Các tuần {missing[:8]}{'…' if len(missing) > 8 else ''} "
                        "nằm giữa các tuần có dữ liệu nhưng web không có slot nào. "
                        "Có thể bạn chưa nhập, hoặc data đã bị xóa."
                    ),
                    "details": {"weeks": missing},
                })

        # 2. PPCT gap — reuse breakdown nếu caller đã tính, tránh O(n²)
        if breakdown is None:
            breakdown = PatternAnalyzer.compute_subject_breakdown(weeks_data)
        groups_with_gaps = []
        for key, bd in breakdown.items():
            if bd["gaps"]:
                p = report.subject_progress.get(key)
                if p:
                    groups_with_gaps.append({
                        "key": key,
                        "label": f"{p.lop_text or p.lop_id} / {p.phan_mon_text or p.phan_mon_id}",
                        "gaps": bd["gaps"],
                        "max_ppct": p.last_ppct,
                    })
        if groups_with_gaps:
            for g in groups_with_gaps[:10]:
                gaps_text = ", ".join(str(x) for x in g["gaps"][:8])
                if len(g["gaps"]) > 8:
                    gaps_text += f" … (+{len(g['gaps']) - 8})"
                warnings.append({
                    "type": "ppct_gap",
                    "severity": SEVERITY_WARNING,
                    "title": f"Thiếu PPCT: {g['label']}",
                    "message": (
                        f"Web đã có PPCT đến {g['max_ppct']} cho nhóm này, "
                        f"nhưng thiếu các tiết: {gaps_text}. "
                        "Có thể do bạn nhập không liên tục."
                    ),
                    "details": g,
                })

        # 3. Pattern lẻ/chẵn — group "le_only" hoặc "chan_only" mà số tuần
        #    đáng kể (≥3) → đáng chú ý vì có thể user dạy đặc thù lẻ/chẵn
        for key, bd in breakdown.items():
            if bd["pattern"] in (PATTERN_LE_ONLY, PATTERN_CHAN_ONLY):
                # Chỉ báo nếu có ≥3 tuần (tránh noise)
                weeks_count = (
                    len(bd["le_weeks"]) if bd["pattern"] == PATTERN_LE_ONLY
                    else len(bd["chan_weeks"])
                )
                if weeks_count >= 3:
                    p = report.subject_progress.get(key)
                    if p:
                        warnings.append({
                            "type": "pattern_le_chan",
                            "severity": SEVERITY_INFO,
                            "title": (
                                f"Chỉ tuần {'lẻ' if bd['pattern'] == PATTERN_LE_ONLY else 'chẵn'}: "
                                f"{p.lop_text or p.lop_id} / {p.phan_mon_text or p.phan_mon_id}"
                            ),
                            "message": (
                                f"Nhóm này chỉ xuất hiện ở tuần "
                                f"{'lẻ' if bd['pattern'] == PATTERN_LE_ONLY else 'chẵn'} "
                                f"({weeks_count} tuần). Đảm bảo profile của "
                                "bạn cũng đặt slot này đúng tuần lẻ/chẵn."
                            ),
                            "details": {"key": key, "pattern": bd["pattern"]},
                        })

        # 4 + 5. So sánh với profile (nếu có)
        if profile is not None:
            profile_keys = set()
            for entry in (profile.ppct_starts or []):
                profile_keys.add(entry.group_key)
            web_keys = set(report.subject_progress.keys())

            # Web có nhưng profile không có
            orphan_in_profile = web_keys - profile_keys
            if orphan_in_profile:
                names = []
                for k in list(orphan_in_profile)[:5]:
                    p = report.subject_progress.get(k)
                    if p:
                        names.append(
                            f"{p.lop_text or p.lop_id}/{p.phan_mon_text or p.phan_mon_id}"
                        )
                warnings.append({
                    "type": "group_orphan_in_profile",
                    "severity": SEVERITY_WARNING,
                    "title": f"{len(orphan_in_profile)} nhóm web không có trong profile",
                    "message": (
                        f"Web có data cho: {'; '.join(names)}"
                        + (f" và {len(orphan_in_profile) - 5} nhóm khác"
                           if len(orphan_in_profile) > 5 else "")
                        + ". Có thể bạn đã bỏ khỏi profile nhưng web còn data cũ."
                    ),
                    "details": {"keys": list(orphan_in_profile)},
                })

            # Profile có nhưng web không có
            missing_on_web = profile_keys - web_keys
            if missing_on_web:
                names = []
                for k in list(missing_on_web)[:5]:
                    for entry in profile.ppct_starts:
                        if entry.group_key == k:
                            names.append(
                                f"{entry.lop_text}/{entry.phan_mon_text}"
                            )
                            break
                warnings.append({
                    "type": "group_missing_on_web",
                    "severity": SEVERITY_INFO,
                    "title": f"{len(missing_on_web)} nhóm profile chưa có data web",
                    "message": (
                        f"Profile của bạn có: {'; '.join(names)}"
                        + (f" và {len(missing_on_web) - 5} nhóm khác"
                           if len(missing_on_web) > 5 else "")
                        + ". Các nhóm này chưa từng được nhập trên web."
                    ),
                    "details": {"keys": list(missing_on_web)},
                })

        # 6. PPCT duplicate trong 1 tuần
        for tuan, wd in weeks_data.items():
            seen: dict[str, list[int]] = {}
            for slot in wd.filled_slots:
                if not slot.lop_id or not slot.phan_mon_id:
                    continue
                try:
                    ppct = int(slot.ppct)
                except (ValueError, TypeError):
                    continue
                if ppct <= 0:
                    continue
                key = f"{slot.lop_id}|{slot.mon_id}|{slot.phan_mon_id}"
                seen.setdefault(key, []).append(ppct)
            for key, ppcts in seen.items():
                if len(ppcts) != len(set(ppcts)):
                    p = report.subject_progress.get(key)
                    if p:
                        # Tìm các PPCT bị duplicate
                        counts = Counter(ppcts)
                        dups = sorted([
                            ppct for ppct, c in counts.items() if c > 1
                        ])
                        warnings.append({
                            "type": "ppct_duplicate",
                            "severity": SEVERITY_ERROR,
                            "title": (
                                f"PPCT trùng tại tuần {tuan}: "
                                f"{p.lop_text or p.lop_id} / "
                                f"{p.phan_mon_text or p.phan_mon_id}"
                            ),
                            "message": (
                                f"Có nhiều slot cùng PPCT {dups} trong 1 tuần. "
                                "Server thường reject save trong tình huống này."
                            ),
                            "details": {"key": key, "tuan": tuan, "dups": dups},
                        })

        # Sort warnings theo severity (error > warning > info)
        warnings.sort(
            key=lambda w: (
                _SEVERITY_ORDER.get(w.get("severity"), 9),
                w.get("title", ""),
            )
        )
        return warnings

    @staticmethod
    def analyze_health_in_scope(
        weeks_data: dict[int, "WeekData"],
        allowed_groups: set[str],
        expected_slot_count_by_week: dict[int, dict[str, int]] | None = None,
    ) -> HealthScanResult:
        """Quét tình trạng KHDH chỉ trong scope group user có quyền nhập.

        Scope = group_key xuất hiện trong profile/template hiện tại.

        Detect 4 loại issue:
          - PPCT gap
          - PPCT duplicate
          - Slot thiếu so với template active của tuần
          - Slot thừa so với template active của tuần
        """
        result = HealthScanResult(
            allowed_groups=set(allowed_groups or set()),
            scanned_weeks=sorted(weeks_data.keys()),
            weeks_data=weeks_data,
        )
        if not result.allowed_groups or not weeks_data:
            return result

        # group_key -> list[(tuan, thu, buoi, tiet, ppct, ten_bai)]
        history: dict[str, list[tuple[int, int, int, int, int, str]]] = defaultdict(list)
        # week -> group -> slot_count
        actual_slot_count_by_week: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))

        for tuan, wd in weeks_data.items():
            for slot in wd.filled_slots:
                if not slot.lop_id or not slot.phan_mon_id:
                    continue
                group_key = f"{slot.lop_id}|{(slot.mon_id or slot.mon_dmonid)}|{slot.phan_mon_id}"
                if group_key not in result.allowed_groups:
                    continue
                try:
                    ppct = int(slot.ppct)
                except (ValueError, TypeError):
                    continue
                if ppct <= 0:
                    continue
                result.actual_groups_seen.add(group_key)
                result.total_slots_scanned += 1
                history[group_key].append(
                    (tuan, slot.thu, slot.buoi_idx, slot.tiet_idx, ppct, slot.ten_bai)
                )
                actual_slot_count_by_week[tuan][group_key] += 1

        # 0. Group trong scope nhưng hoàn toàn không có data trên web
        no_data_groups = sorted(result.allowed_groups - result.actual_groups_seen)
        for group_key in no_data_groups:
            lop_id, mon_id, phan_mon_id = group_key.split("|", 2)
            result.issues.append(HealthIssue(
                kind=HEALTH_KIND_GROUP_NO_DATA,
                severity="warn",
                group_key=group_key,
                lop_id=lop_id,
                lop_text=lop_id,
                mon_id=mon_id,
                mon_text=mon_id,
                phan_mon_id=phan_mon_id,
                phan_mon_text=phan_mon_id,
                message="Không thấy bất kỳ dữ liệu nào trên web cho group này trong dải tuần đã quét.",
            ))

        # 1. PPCT gap + duplicate per group
        for group_key, items in history.items():
            if not items:
                continue
            items_sorted = sorted(items, key=lambda x: (x[0], x[1], x[2], x[3], x[4]))
            sample = items_sorted[0]
            lop_id, mon_id, phan_mon_id = group_key.split("|", 2)

            # Resolve display labels from any matching slot in history
            lop_text = lop_id
            mon_text = mon_id
            phan_mon_text = phan_mon_id
            found_labels = False
            for tuan, wd in weeks_data.items():
                for slot in wd.filled_slots:
                    gk = f"{slot.lop_id}|{slot.mon_id}|{slot.phan_mon_id}"
                    if gk == group_key:
                        lop_text = slot.lop_text or lop_id
                        mon_text = slot.mon_text or mon_id
                        phan_mon_text = slot.phan_mon_text or phan_mon_id
                        found_labels = True
                        break
                if found_labels:
                    break

            # duplicate check: same ppct appears more than once in scope history
            ppct_counts = Counter(ppct for _, _, _, _, ppct, _ in items_sorted)
            for dup_ppct, count in ppct_counts.items():
                if count > 1:
                    dup_weeks = sorted({t for t, _, _, _, p, _ in items_sorted if p == dup_ppct})
                    result.issues.append(HealthIssue(
                        kind=HEALTH_KIND_PPCT_DUPLICATE,
                        severity="error",
                        group_key=group_key,
                        lop_id=lop_id,
                        lop_text=lop_text,
                        mon_id=mon_id,
                        mon_text=mon_text,
                        phan_mon_id=phan_mon_id,
                        phan_mon_text=phan_mon_text,
                        week_from=min(dup_weeks),
                        week_to=max(dup_weeks),
                        duplicate_ppct=dup_ppct,
                        message=f"PPCT {dup_ppct} bị trùng {count} lần trong scope đã quét.",
                    ))

            prev_ppct = None
            prev_tuan = 0
            for tuan, thu, buoi, tiet, ppct, _ in items_sorted:
                if prev_ppct is None:
                    prev_ppct = ppct
                    prev_tuan = tuan
                    continue
                if ppct > prev_ppct + 1:
                    missing = list(range(prev_ppct + 1, ppct))
                    sev = "warn" if len(missing) <= 2 else "error"
                    result.issues.append(HealthIssue(
                        kind=HEALTH_KIND_PPCT_GAP,
                        severity=sev,
                        group_key=group_key,
                        lop_id=lop_id,
                        lop_text=lop_text,
                        mon_id=mon_id,
                        mon_text=mon_text,
                        phan_mon_id=phan_mon_id,
                        phan_mon_text=phan_mon_text,
                        week_from=prev_tuan,
                        week_to=tuan,
                        ppct_from=prev_ppct,
                        ppct_to=ppct,
                        missing_ppcts=missing,
                        message=(
                            f"PPCT nhảy từ {prev_ppct} → {ppct}, thiếu {missing}."
                        ),
                    ))
                prev_ppct = max(prev_ppct, ppct)
                prev_tuan = tuan

        # 2. Slot thiếu/thừa theo template active của tuần
        if expected_slot_count_by_week:
            all_weeks = sorted(expected_slot_count_by_week.keys())
            for tuan in all_weeks:
                expected_map = expected_slot_count_by_week.get(tuan, {})
                actual_map = actual_slot_count_by_week.get(tuan, {})
                for group_key, expected_count in expected_map.items():
                    if group_key not in result.allowed_groups:
                        continue
                    actual_count = int(actual_map.get(group_key, 0))
                    if actual_count == expected_count:
                        continue
                    lop_id, mon_id, phan_mon_id = group_key.split("|", 2)
                    # best effort labels
                    lop_text = lop_id
                    mon_text = mon_id
                    phan_mon_text = phan_mon_id
                    for wd in weeks_data.values():
                        for slot in wd.filled_slots:
                            if f"{slot.lop_id}|{slot.mon_id}|{slot.phan_mon_id}" == group_key:
                                lop_text = slot.lop_text or lop_id
                                mon_text = slot.mon_text or mon_id
                                phan_mon_text = slot.phan_mon_text or phan_mon_id
                                break
                        else:
                            continue
                        break

                    if actual_count < expected_count:
                        result.issues.append(HealthIssue(
                            kind=HEALTH_KIND_SLOT_MISSING,
                            severity="warn",
                            group_key=group_key,
                            lop_id=lop_id,
                            lop_text=lop_text,
                            mon_id=mon_id,
                            mon_text=mon_text,
                            phan_mon_id=phan_mon_id,
                            phan_mon_text=phan_mon_text,
                            week_from=tuan,
                            week_to=tuan,
                            expected_slots=expected_count,
                            actual_slots=actual_count,
                            message=f"Thiếu {expected_count - actual_count} slot so với template tuần {tuan}.",
                        ))
                    else:
                        result.issues.append(HealthIssue(
                            kind=HEALTH_KIND_SLOT_EXTRA,
                            severity="warn",
                            group_key=group_key,
                            lop_id=lop_id,
                            lop_text=lop_text,
                            mon_id=mon_id,
                            mon_text=mon_text,
                            phan_mon_id=phan_mon_id,
                            phan_mon_text=phan_mon_text,
                            week_from=tuan,
                            week_to=tuan,
                            expected_slots=expected_count,
                            actual_slots=actual_count,
                            message=f"Thừa {actual_count - expected_count} slot so với template tuần {tuan}.",
                        ))

        result.issues.sort(
            key=lambda x: (
                {"error": 0, "warn": 1, "ok": 2}.get(x.severity, 9),
                x.lop_text,
                x.mon_text,
                x.phan_mon_text,
                x.week_from,
                x.ppct_from,
            )
        )
        return result

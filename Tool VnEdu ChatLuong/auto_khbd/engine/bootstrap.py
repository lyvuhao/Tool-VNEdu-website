"""Tải dữ liệu khởi tạo (lớp, môn, PPCT, TKB) từ trang KHDH."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TYPE_CHECKING

from ..log import logger

if TYPE_CHECKING:  # chỉ dùng cho chú thích kiểu
    from .client.client import KHDHClient


# ######################################################################
# Section: bootstrap
# ######################################################################






# =====================================================================
# Helpers
# =====================================================================

def _normalize_text(value: Any) -> str:
    """Bỏ dấu, lowercase, gộp khoảng trắng — dùng cho lookup case-insensitive."""
    s = str(value or "").strip()
    if not s:
        return ""
    # Đặc biệt: đ/Đ là ký tự độc lập (U+0111/U+0110) — NFD không tách được
    s = s.replace("đ", "d").replace("Đ", "D")
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = re.sub(r"\s+", " ", s)
    return s.lower()


# =====================================================================
# BootstrapData
# =====================================================================

@dataclass
class BootstrapData:
    """Snapshot danh sách lớp/môn/phân môn fetch từ web.

    Cấu trúc dữ liệu:
        lop_options:           [{"id": str, "text": str, "khoi": str}, ...]
        mon_by_lop:            {lop_id: [{"id": str, "text": str, "dmonid": str}, ...]}
        phan_mon_by_lop_mon:   {f"{lop_id}|{mon_id}": [{"id": str, "text": str}, ...]}
        a_phan_mon_global:     [<raw record từ aPhanMon>, ...]

    `a_phan_mon_global` giữ lại để debug + fallback resolve khi web đổi
    cấu trúc nội bộ.
    """
    lop_options: list[dict[str, str]] = field(default_factory=list)
    mon_by_lop: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    phan_mon_by_lop_mon: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    a_phan_mon_global: list[dict[str, Any]] = field(default_factory=list)

    fetched_at: str = ""        # ISO timestamp
    nam_hoc: int = 0
    cap_hoc: int = 0
    sample_tuan: int = 0        # tuần đã sample để fetch

    # ----------------------------------------------------------
    # Lookup helpers — dùng cho UI (giáo viên gõ tên, app trả ID)
    # ----------------------------------------------------------

    def lookup_lop_id_by_text(self, text: str) -> str | None:
        """Tìm lop_id theo tên lớp (case-insensitive, bỏ dấu)."""
        norm = _normalize_text(text)
        if not norm:
            return None
        for o in self.lop_options:
            if _normalize_text(o.get("text", "")) == norm:
                return str(o.get("id", ""))
        return None

    def lookup_mon_id_by_text(self, lop_id: str, text: str) -> str | None:
        """Tìm mon_id trong lớp `lop_id` theo tên môn."""
        norm = _normalize_text(text)
        if not norm:
            return None
        for o in self.mon_by_lop.get(str(lop_id), []):
            if _normalize_text(o.get("text", "")) == norm:
                return str(o.get("id", ""))
        return None

    def lookup_phan_mon_id_by_text(self, lop_id: str, mon_id: str,
                                    text: str) -> str | None:
        """Tìm phan_mon_id trong (lop, mon) theo tên phân môn."""
        norm = _normalize_text(text)
        if not norm:
            return None
        key = f"{lop_id}|{mon_id}"
        for o in self.phan_mon_by_lop_mon.get(key, []):
            if _normalize_text(o.get("text", "")) == norm:
                return str(o.get("id", ""))
        # Fallback: tìm trong aPhanMon global khớp đúng tên
        for pm in self.a_phan_mon_global:
            if (str(pm.get("mon_hoc_id", "")) == str(mon_id)
                    and _normalize_text(pm.get("ten", "")) == norm):
                return str(pm.get("id", ""))
        return None

    def get_mon_options(self, lop_id: str) -> list[dict[str, str]]:
        """Trả list môn cho 1 lớp. Empty list nếu không có."""
        return list(self.mon_by_lop.get(str(lop_id), []))

    def get_phan_mon_options(self, lop_id: str, mon_id: str) -> list[dict[str, str]]:
        """Trả list phân môn cho (lop, mon)."""
        return list(self.phan_mon_by_lop_mon.get(f"{lop_id}|{mon_id}", []))

    def has_lop(self, lop_id: str) -> bool:
        return any(str(o.get("id", "")) == str(lop_id) for o in self.lop_options)

    def has_mon(self, lop_id: str, mon_id: str) -> bool:
        return any(str(o.get("id", "")) == str(mon_id)
                   for o in self.get_mon_options(lop_id))

    def has_phan_mon(self, lop_id: str, mon_id: str, phan_mon_id: str) -> bool:
        return any(str(o.get("id", "")) == str(phan_mon_id)
                   for o in self.get_phan_mon_options(lop_id, mon_id))

    def is_fresh(self, max_age_hours: int = 24) -> bool:
        """True nếu fetched_at < max_age_hours."""
        if not self.fetched_at:
            return False
        try:
            fetched = datetime.fromisoformat(self.fetched_at)
        except ValueError:
            return False
        delta = datetime.now() - fetched
        return delta.total_seconds() < max_age_hours * 3600

    def is_empty(self) -> bool:
        """True nếu không có lớp nào — giáo viên chưa được phân công."""
        return len(self.lop_options) == 0

    # ----------------------------------------------------------
    # Serialize cho JSON cache
    # ----------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "lop_options": list(self.lop_options),
            "mon_by_lop": {k: list(v) for k, v in self.mon_by_lop.items()},
            "phan_mon_by_lop_mon": {k: list(v) for k, v in self.phan_mon_by_lop_mon.items()},
            "a_phan_mon_global": list(self.a_phan_mon_global),
            "fetched_at": self.fetched_at,
            "nam_hoc": self.nam_hoc,
            "cap_hoc": self.cap_hoc,
            "sample_tuan": self.sample_tuan,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "BootstrapData":
        if not isinstance(data, dict):
            return cls()
        if data.get("version") != 1:
            return cls()
        return cls(
            lop_options=list(data.get("lop_options") or []),
            mon_by_lop={k: list(v) for k, v in (data.get("mon_by_lop") or {}).items()},
            phan_mon_by_lop_mon={
                k: list(v)
                for k, v in (data.get("phan_mon_by_lop_mon") or {}).items()
            },
            a_phan_mon_global=list(data.get("a_phan_mon_global") or []),
            fetched_at=str(data.get("fetched_at") or ""),
            nam_hoc=int(data.get("nam_hoc") or 0),
            cap_hoc=int(data.get("cap_hoc") or 0),
            sample_tuan=int(data.get("sample_tuan") or 0),
        )


# =====================================================================
# fetch_bootstrap — main entrypoint
# =====================================================================

def build_from_week_data(week_data, nam_hoc: int = 0, cap_hoc: int = 0) -> BootstrapData:
    """Build BootstrapData từ 1 WeekData đã parse (offline / cho test).

    Dùng cho unit test — nhận trực tiếp `WeekData` thay vì gọi API.

    Args:
        week_data: instance khdh_engine.parser.WeekData
        nam_hoc, cap_hoc: gán vào output (không ảnh hưởng logic)
    """
    out = BootstrapData(
        nam_hoc=nam_hoc,
        cap_hoc=cap_hoc,
        sample_tuan=int(getattr(week_data, "tuan", 0) or 0),
        fetched_at=datetime.now().isoformat(timespec="seconds"),
    )
    out.a_phan_mon_global = list(getattr(week_data, "a_phan_mon", []) or [])

    # Build lop_options + mon_by_lop từ slot options
    seen_lop: dict[str, dict[str, str]] = {}
    seen_mon_by_lop: dict[str, dict[str, dict[str, str]]] = {}
    seen_pm_by_lop_mon: dict[str, dict[str, dict[str, str]]] = {}

    for slot in getattr(week_data, "slots", []) or []:
        # lop_options từ select cboLopHoc — dùng slot.mon_options + lop info
        # Ở parser, mỗi slot không có full lop_options, mà có lop_id/lop_text gắn vào.
        # Để build full list, ta walk qua tất cả slot và collect duy nhất.
        if slot.lop_id and slot.lop_id != "0" and slot.lop_text:
            if slot.lop_id not in seen_lop:
                seen_lop[slot.lop_id] = {
                    "id": str(slot.lop_id),
                    "text": str(slot.lop_text),
                    "khoi": str(slot.khoi or ""),
                }

        # mon_by_lop — từ slot.mon_options (mỗi cell select đã có sẵn)
        if slot.lop_id and slot.lop_id != "0":
            mon_dict = seen_mon_by_lop.setdefault(slot.lop_id, {})
            for opt in slot.mon_options or []:
                opt_id = str(opt.get("value") or "").strip()
                opt_txt = str(opt.get("text") or "").strip()
                if not opt_id or opt_id == "0" or not opt_txt or opt_txt == "---":
                    continue
                if opt_id not in mon_dict:
                    mon_dict[opt_id] = {
                        "id": opt_id, "text": opt_txt,
                        "dmonid": str(slot.mon_dmonid or ""),
                    }

            # Nếu slot có mon_id mà mon_options chưa list (web đôi khi vậy),
            # vẫn thêm từ slot info
            if slot.mon_id and slot.mon_id != "0" and slot.mon_text:
                if slot.mon_id not in mon_dict:
                    mon_dict[slot.mon_id] = {
                        "id": str(slot.mon_id),
                        "text": str(slot.mon_text),
                        "dmonid": str(slot.mon_dmonid or ""),
                    }

        # phan_mon_by_lop_mon từ slot.phan_mon_options
        if slot.lop_id and slot.lop_id != "0" and slot.mon_id and slot.mon_id != "0":
            key = f"{slot.lop_id}|{slot.mon_id}"
            pm_dict = seen_pm_by_lop_mon.setdefault(key, {})
            for opt in slot.phan_mon_options or []:
                opt_id = str(opt.get("value") or "").strip()
                opt_txt = str(opt.get("text") or "").strip()
                if not opt_id or opt_id == "0" or not opt_txt or opt_txt == "---":
                    continue
                if opt_id not in pm_dict:
                    pm_dict[opt_id] = {"id": opt_id, "text": opt_txt}

            # Slot's own phan_mon (nếu select chưa có option cho nó)
            if slot.phan_mon_id and slot.phan_mon_id != "0" and slot.phan_mon_text:
                if slot.phan_mon_id not in pm_dict:
                    pm_dict[slot.phan_mon_id] = {
                        "id": str(slot.phan_mon_id),
                        "text": str(slot.phan_mon_text),
                    }

    # Cuối cùng, enrich phan_mon từ aPhanMon global cho mỗi (lop, mon)
    # Nếu (lop, mon) chưa có phân môn nào → lookup trong aPhanMon theo mon_hoc_id
    # và filter theo khối của lớp
    lop_id_to_khoi: dict[str, str] = {
        lop_id: info.get("khoi", "")
        for lop_id, info in seen_lop.items()
    }

    # 1) Enrich danh sách MÔN cho mỗi lớp từ aPhanMon (filter theo khối)
    #    Slot.mon_options chỉ trả về môn TKB đã gán → thường chỉ 1-2 môn/lớp.
    #    aPhanMon trả full danh sách môn của trường + có khoi_hoc_mon, dùng làm
    #    nguồn chuẩn để giáo viên chọn được mọi môn họ dạy.
    for lop_id, khoi in lop_id_to_khoi.items():
        if not khoi:
            continue
        mon_dict = seen_mon_by_lop.setdefault(lop_id, {})
        for pm in out.a_phan_mon_global:
            mon_id = str(pm.get("mon_hoc_id", "") or "").strip()
            ten_mon = str(pm.get("ten_mon", "") or "").strip()
            khoi_field = str(pm.get("khoi_hoc_mon", "") or "")
            if not mon_id or mon_id == "0" or not ten_mon:
                continue
            # Filter theo khối nếu khoi_hoc_mon có cấu trúc "-6-7-8-9-..."
            if khoi_field and f"-{khoi}-" not in khoi_field:
                continue
            if mon_id not in mon_dict:
                mon_dict[mon_id] = {
                    "id": mon_id, "text": ten_mon, "dmonid": mon_id,
                }

    # 2) Enrich PHÂN MÔN cho mỗi (lop, mon) từ aPhanMon
    for lop_id, mon_dict in seen_mon_by_lop.items():
        khoi = lop_id_to_khoi.get(lop_id, "")
        for mon_id in mon_dict.keys():
            key = f"{lop_id}|{mon_id}"
            pm_dict = seen_pm_by_lop_mon.setdefault(key, {})
            for pm in out.a_phan_mon_global:
                pm_mon_id = str(pm.get("mon_hoc_id", ""))
                if pm_mon_id != str(mon_id):
                    continue
                # Nếu khoi_hoc_mon có, filter theo khối
                khoi_field = str(pm.get("khoi_hoc_mon", "") or "")
                if khoi and khoi_field and f"-{khoi}-" not in khoi_field:
                    continue
                pm_id = str(pm.get("id", ""))
                pm_text = str(pm.get("ten", "") or pm.get("sten", "") or "")
                if not pm_id or not pm_text:
                    continue
                if pm_id not in pm_dict:
                    pm_dict[pm_id] = {"id": pm_id, "text": pm_text}

    # Convert dict-of-dict → list-of-dict, sort theo text
    out.lop_options = sorted(
        seen_lop.values(),
        key=lambda o: _normalize_text(o.get("text", ""))
    )
    out.mon_by_lop = {
        lop_id: sorted(mon.values(),
                      key=lambda o: _normalize_text(o.get("text", "")))
        for lop_id, mon in seen_mon_by_lop.items()
    }
    out.phan_mon_by_lop_mon = {
        key: sorted(pm.values(),
                   key=lambda o: _normalize_text(o.get("text", "")))
        for key, pm in seen_pm_by_lop_mon.items()
    }
    return out


def fetch_bootstrap(client: "KHDHClient",
                     tuan_for_sample: int | None = None) -> BootstrapData:
    """Fetch BootstrapData từ web qua client.

    Args:
        client: KHDHClient đã connect và đăng nhập.
        tuan_for_sample: tuần để sample fetch. Mặc định = client.fetch_context().tuan_hoc
                         hoặc 1 nếu không có.

    Logic:
        1. Đảm bảo context đã đọc xong (token, gv_id, win_id)
        2. Tạm bật "Sửa (Gợi ý theo TKB)" để fetch_week trả aPhanMon đầy đủ
        3. Sample 1 tuần để có aPhanMon + slot options
        4. Build BootstrapData từ WeekData
        5. Đặt lại mode "Sửa" để giáo viên thấy đúng UI khi quay lại web

    Returns:
        BootstrapData. `is_empty()` == True nếu không có lớp nào.
    """
    ctx = client.fetch_context()

    # Bật mode để slot có lớp/môn options đầy đủ
    try:
        client.enable_goi_y_tkb_mode()
    except Exception as e:
        logger.warning(f"enable_goi_y_tkb_mode failed (continuing): {e}")

    # try/finally đảm bảo: dù fetch_week có lỗi, web vẫn được đặt về mode "Sửa"
    # để giáo viên thấy đúng UI khi quay lại web (tránh đứng ở "Sửa (Gợi ý)").
    try:
        # Pick sample tuần
        if tuan_for_sample is None or tuan_for_sample <= 0:
            # Đọc tuần hiện tại từ combobox cboTuanHoc qua client.page
            try:
                sample = client.page.evaluate(
                    "() => { try { const c = Ext.ComponentQuery.query('combobox')"
                    ".find(c => (c.getName?c.getName():c.name) === 'cboTuanHoc');"
                    " return c ? c.getValue() : 0; } catch(e) { return 0; } }"
                )
                tuan_for_sample = int(sample or 0) or 1
            except Exception:
                tuan_for_sample = 1

        logger.info(f"fetch_bootstrap: sampling tuần {tuan_for_sample}")
        week_data = client.fetch_week(tuan_for_sample, is_edit=2)

        out = build_from_week_data(week_data, nam_hoc=ctx.nam_hoc, cap_hoc=ctx.cap_hoc)
        out.sample_tuan = tuan_for_sample

        # FIX: Enrich lop_options từ DOM cboLopHoc — đây là source of truth
        # build_from_week_data chỉ collect lớp từ slot có data → thiếu lớp
        # chưa nhập. DOM cboLopHoc chứa TẤT CẢ lớp giáo viên được phân công.
        try:
            dom_lops = client.page.evaluate("""
() => {
    var el = document.querySelector('[id^="cboLopHoc_"]');
    if (!el) return [];
    var opts = [];
    for (var i = 0; i < el.options.length; i++) {
        var v = el.options[i].value;
        var t = el.options[i].text;
        var k = el.options[i].getAttribute('khoi') || '';
        if (v && v !== '0' && t && t !== '---') {
            opts.push({id: v, text: t, khoi: k});
        }
    }
    return opts;
}
""")
            if dom_lops:
                # Merge vào lop_options — ưu tiên giữ entry đã có (có khoi)
                existing_ids = {o["id"] for o in out.lop_options}
                new_lop_ids = []
                for dom_lop in dom_lops:
                    if dom_lop["id"] not in existing_ids:
                        out.lop_options.append({
                            "id": dom_lop["id"],
                            "text": dom_lop["text"],
                            "khoi": dom_lop.get("khoi", ""),
                        })
                        existing_ids.add(dom_lop["id"])
                        new_lop_ids.append(dom_lop["id"])
                    else:
                        # Update khoi nếu chưa có
                        for o in out.lop_options:
                            if o["id"] == dom_lop["id"] and not o.get("khoi"):
                                o["khoi"] = dom_lop.get("khoi", "")

                # Enrich mon_by_lop cho các lớp mới thêm từ DOM
                # Dùng mon_by_lop của lớp đã có cùng khối làm template.
                # Nếu không có lớp cùng khối → fetch mon options từ DOM
                # bằng cách set lop_id vào cboLopHoc và đọc cboMonHoc.
                import time as _time
                for new_lop_id in new_lop_ids:
                    new_lop_info = next(
                        (o for o in out.lop_options if o["id"] == new_lop_id), None
                    )
                    if not new_lop_info:
                        continue
                    new_khoi = new_lop_info.get("khoi", "")
                    # Tìm lớp cùng khối đã có mon_by_lop
                    template_mons = None
                    template_lop_id = None
                    for existing_lop_id, mons in out.mon_by_lop.items():
                        existing_info = next(
                            (o for o in out.lop_options if o["id"] == existing_lop_id), None
                        )
                        if existing_info and existing_info.get("khoi") == new_khoi:
                            template_mons = mons
                            template_lop_id = existing_lop_id
                            break

                    if not template_mons:
                        # Không có lớp cùng khối → set lop vào DOM và đọc mon options
                        try:
                            client.page.evaluate("""
(args) => {
    var el = document.querySelector('[id^="cboLopHoc_"]');
    if (!el) return;
    for (var i = 0; i < el.options.length; i++) {
        if (String(el.options[i].value) === String(args.lop_id)) {
            el.selectedIndex = i;
            el.dispatchEvent(new Event('change', {bubbles: true}));
            try { if (window.$) window.$(el).trigger('change'); } catch(e) {}
            break;
        }
    }
}
""", {"lop_id": new_lop_id})
                            _time.sleep(0.8)
                            dom_mons = client.page.evaluate("""
() => {
    var el = document.querySelector('[id^="cboMonHoc_"]');
    if (!el) return [];
    var opts = [];
    for (var i = 0; i < el.options.length; i++) {
        var v = el.options[i].value;
        var t = el.options[i].text;
        if (v && v !== '0' && t && t !== '---') {
            opts.push({id: v, text: t});
        }
    }
    return opts;
}
""")
                            if dom_mons:
                                template_mons = dom_mons
                                template_lop_id = new_lop_id
                        except Exception as e:
                            logger.warning(f"fetch_bootstrap: DOM mon fetch for {new_lop_id} failed: {e}")

                    if template_mons:
                        out.mon_by_lop[new_lop_id] = list(template_mons)
                        # Enrich phan_mon_by_lop_mon cho lớp mới
                        if template_lop_id and template_lop_id != new_lop_id:
                            for mon in template_mons:
                                old_key = f"{template_lop_id}|{mon['id']}"
                                new_key = f"{new_lop_id}|{mon['id']}"
                                if old_key in out.phan_mon_by_lop_mon and new_key not in out.phan_mon_by_lop_mon:
                                    out.phan_mon_by_lop_mon[new_key] = list(
                                        out.phan_mon_by_lop_mon[old_key]
                                    )
                        else:
                            # Lớp mới fetch từ DOM — enrich phan_mon từ aPhanMon global
                            lop_khoi = new_lop_info.get("khoi", "")
                            for mon in template_mons:
                                new_key = f"{new_lop_id}|{mon['id']}"
                                if new_key not in out.phan_mon_by_lop_mon:
                                    pm_list = []
                                    for pm in out.a_phan_mon_global:
                                        pm_mon_id = str(pm.get("mon_hoc_id", ""))
                                        if pm_mon_id != str(mon["id"]):
                                            continue
                                        khoi_field = str(pm.get("khoi_hoc_mon", "") or "")
                                        if lop_khoi and khoi_field and f"-{lop_khoi}-" not in khoi_field:
                                            continue
                                        pm_id = str(pm.get("id", ""))
                                        pm_text = str(pm.get("ten", "") or pm.get("sten", "") or "")
                                        if pm_id and pm_text:
                                            pm_list.append({"id": pm_id, "text": pm_text})
                                    if pm_list:
                                        out.phan_mon_by_lop_mon[new_key] = pm_list

                # Re-sort lop_options
                out.lop_options = sorted(
                    out.lop_options,
                    key=lambda o: _normalize_text(o.get("text", ""))
                )
                logger.info(
                    f"fetch_bootstrap: enriched lop_options from DOM: "
                    f"{len(out.lop_options)} lớp, "
                    f"{len(new_lop_ids)} lớp mới thêm"
                )
        except Exception as e:
            logger.warning(f"fetch_bootstrap: DOM lop enrich failed: {e}")
    finally:
        # Đặt lại mode "Sửa" — luôn chạy, kể cả khi fetch_week throw.
        try:
            client.enable_edit_mode()
        except Exception:
            pass

    return out

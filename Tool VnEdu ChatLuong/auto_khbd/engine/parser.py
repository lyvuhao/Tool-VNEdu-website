"""Parse response `load` của trang KHDH VnEdu thành dữ liệu tuần/tiết."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any


# ######################################################################
# Section: parser
# ######################################################################




# ---------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------

@dataclass
class SlotData:
    """Dữ liệu của 1 slot (1 dòng trong bảng KHDH).

    row_key = "thu_buoi_tiet" (vd "2_2_1")
    Slot có thể "filled" (có lop) hoặc "empty" (lop=0/'').
    """
    row_key: str
    thu: int
    buoi_idx: int       # 1=Sáng, 2=Chiều
    tiet_idx: int       # 1..5
    tiet_tkb: str = ""

    # --- TKB-defined ---
    lop_id: str = ""        # lop được TKB gán (có thể trống nếu TKB không xếp)
    lop_text: str = ""
    mon_id: str = ""        # môn được TKB gán
    mon_text: str = ""      # tên môn (vd "Ngoại ngữ", "Hoạt động trải nghiệm…")
    mon_dmonid: str = ""    # `dmonid` attribute trên select cboMonHoc — môn TKB gốc
    khoi: str = ""          # khối học (6/7/8/9)

    # --- KHDH-filled ---
    phan_mon_id: str = ""
    phan_mon_text: str = ""
    ppct: str = ""          # số tiết PPCT (string)
    ten_bai: str = ""
    ghi_chu: str = ""
    trang_thai: str = ""    # "0"=Bình thường, "-1"=---, ...
    trang_thai_text: str = ""
    id_giao_an: str = ""

    # Selects với options (dùng để build catalog phân môn)
    mon_options: list[dict[str, str]] = field(default_factory=list)
    phan_mon_options: list[dict[str, str]] = field(default_factory=list)

    @property
    def has_lop(self) -> bool:
        return bool(self.lop_id) and self.lop_id not in ("0", "")

    @property
    def is_filled(self) -> bool:
        """Slot 'filled' = có lop + môn + PPCT + tên bài."""
        return self.has_lop and bool(self.ppct) and bool(self.ten_bai)


@dataclass
class WeekData:
    """Toàn bộ data của 1 tuần parse từ response."""
    tuan: int                                    # số tuần
    slots: list[SlotData] = field(default_factory=list)
    a_phan_mon: list[dict[str, Any]] = field(default_factory=list)
    a_mon_hoc: list[dict[str, Any]] = field(default_factory=list)
    a_lop_hoc: list[dict[str, Any]] = field(default_factory=list)
    html_length: int = 0
    parse_warnings: list[str] = field(default_factory=list)

    # ------ helpers ------
    @property
    def filled_slots(self) -> list[SlotData]:
        return [s for s in self.slots if s.is_filled]

    @property
    def tkb_slots(self) -> list[SlotData]:
        """Slots TKB đã gán lớp (kể cả chưa nhập tên bài)."""
        return [s for s in self.slots if s.has_lop]

    def slot_by_key(self, row_key: str) -> SlotData | None:
        for s in self.slots:
            if s.row_key == row_key:
                return s
        return None


# ---------------------------------------------------------------
# HTMLParser implementation
# ---------------------------------------------------------------

class _TableExtractor(HTMLParser):
    """Extract <tr data_stt>, <select>, <textarea>, <input> từ response HTML."""

    def __init__(self):
        super().__init__()
        self.tr_rows: list[dict[str, str]] = []  # mỗi tr: {data_stt, data_lop_hoc_id, ...}
        self._tr_attrs: dict[str, str] | None = None

        self.selects: dict[str, dict[str, Any]] = {}  # id -> {options:[], dmonid, ...}
        self._cur_select: dict[str, Any] | None = None
        self._cur_options: list[dict[str, Any]] = []

        self._in_option = False
        self._option_attrs: dict[str, str] = {}
        self._option_text: list[str] = []

        self._in_textarea = False
        self._textarea_id = ""
        self._textarea_text: list[str] = []
        self.textareas: dict[str, str] = {}

        self.inputs: dict[str, dict[str, str]] = {}  # id -> {value, type}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        d = {k: (v or "") for (k, v) in attrs}
        if tag == "tr" and "data_stt" in d:
            self._tr_attrs = d
        elif tag == "select":
            self._cur_select = {"id": d.get("id", ""), "class": d.get("class", ""),
                                "dmonid": d.get("dmonid", ""), "name": d.get("name", "")}
            self._cur_options = []
        elif tag == "option" and self._cur_select is not None:
            self._in_option = True
            self._option_attrs = d
            self._option_text = []
        elif tag == "textarea" and "id" in d:
            self._in_textarea = True
            self._textarea_id = d["id"]
            self._textarea_text = []
        elif tag == "input" and "id" in d:
            self.inputs[d["id"]] = {"value": d.get("value", ""), "type": d.get("type", "")}

    def handle_endtag(self, tag: str) -> None:
        if tag == "tr" and self._tr_attrs is not None:
            self.tr_rows.append(self._tr_attrs)
            self._tr_attrs = None
        elif tag == "select" and self._cur_select is not None:
            sel = self._cur_select
            sel["options"] = self._cur_options
            sel["selected"] = next((o for o in self._cur_options if o.get("selected")), None)
            sid = sel.get("id", "")
            if sid:
                self.selects[sid] = sel
            self._cur_select = None
            self._cur_options = []
        elif tag == "option" and self._in_option:
            text = "".join(self._option_text)
            text = text.replace("&nbsp;", " ").replace("\xa0", " ")
            text = re.sub(r"\s+", " ", text).strip()
            opt = {
                "value": self._option_attrs.get("value", ""),
                "text": text,
            }
            if "selected" in self._option_attrs:
                opt["selected"] = True
            # Preserve other custom attributes (vd "khoi" trên cboLopHoc options)
            for k, v in self._option_attrs.items():
                if k not in ("value", "text", "selected"):
                    opt[k] = v
            self._cur_options.append(opt)
            self._in_option = False
            self._option_attrs = {}
            self._option_text = []
        elif tag == "textarea" and self._in_textarea:
            text = "".join(self._textarea_text)
            self.textareas[self._textarea_id] = text.strip()
            self._in_textarea = False
            self._textarea_id = ""
            self._textarea_text = []

    def handle_data(self, data: str) -> None:
        if self._in_option:
            self._option_text.append(data)
        elif self._in_textarea:
            self._textarea_text.append(data)


# ---------------------------------------------------------------
# Public API
# ---------------------------------------------------------------

# Chỉ tìm VỊ TRÍ bắt đầu của mảng (`var aXxx = [`), KHÔNG cố match tới `]`.
# Lý do (bug #3): regex non-greedy `(\[.*?\])` dừng ở `];` ĐẦU TIÊN → cắt cụt
# array nếu một string trong JSON chứa `];` (vd tên bài "Bài [a];b"), gây
# parse sai IM LẶNG (json.loads mảng dở dang hoặc rỗng). Thay vào đó, tìm vị
# trí `[` rồi dùng json.JSONDecoder().raw_decode() để parse theo cấu trúc thật
# (tôn trọng dấu ngoặc lồng nhau + nội dung string).
_RE_VAR_ARRAY_START = {
    "aPhanMon": re.compile(r"var\s+aPhanMon\s*=\s*"),
    "aMonHoc": re.compile(r"var\s+aMonHoc\s*=\s*"),
    "aLopHoc": re.compile(r"var\s+aLopHoc\s*=\s*"),
}


# Map: aPhanMon → a_phan_mon, aMonHoc → a_mon_hoc, aLopHoc → a_lop_hoc
_ARR_ATTR_MAP = {
    "aPhanMon": "a_phan_mon",
    "aMonHoc": "a_mon_hoc",
    "aLopHoc": "a_lop_hoc",
}


_RE_ROW_KEY = re.compile(r"^cboLopHoc_(\d+)_(\d+)_(\d+)$")


# Reuse 1 decoder instance — raw_decode là thread-safe (không giữ state).
_JSON_DECODER = json.JSONDecoder()


def _extract_inline_array(html: str, start_pat: re.Pattern) -> tuple[list | None, str]:
    """Trích 1 mảng JSON inline kiểu `var aXxx = [...];` an toàn.

    Dùng raw_decode parse từ vị trí `[` → tôn trọng cấu trúc JSON thật,
    không bị cắt cụt bởi `];` nằm trong string (bug #3).

    Returns:
        (parsed_list, "") nếu OK, hoặc (None, error_message) nếu fail/không thấy.
    """
    m_start = start_pat.search(html)
    if not m_start:
        return None, ""  # Không có mảng này — không phải lỗi
    # Tìm dấu '[' đầu tiên kể từ vị trí sau dấu '='
    bracket_pos = html.find("[", m_start.end())
    if bracket_pos == -1:
        return None, "no opening bracket"
    try:
        # raw_decode trả (obj, end_index) — parse đúng tới hết mảng JSON,
        # bỏ qua phần đuôi (`; var x=1;` ...).
        obj, _end = _JSON_DECODER.raw_decode(html, bracket_pos)
    except ValueError as e:
        return None, f"{type(e).__name__}: {e}"
    if not isinstance(obj, list):
        return None, f"expected list, got {type(obj).__name__}"
    return obj, ""


def parse_load_response(html: str, tuan: int) -> WeekData:
    """Parse response HTML thành WeekData.

    Args:
        html: response text từ endpoint lich_bao_giang
        tuan: số tuần (đã biết từ request)

    Returns:
        WeekData với slots + a_phan_mon + warnings (nếu có)
    """
    out = WeekData(tuan=tuan, html_length=len(html))

    # 1. Extract inline JS arrays — dùng raw_decode (an toàn với `];` trong string)
    for arr_name, start_pat in _RE_VAR_ARRAY_START.items():
        arr, err = _extract_inline_array(html, start_pat)
        if arr is not None:
            setattr(out, _ARR_ATTR_MAP[arr_name], arr)
        elif err:
            out.parse_warnings.append(f"parse {arr_name} failed: {err}")

    # 1b. Build name lookups từ inline arrays — dùng để enrich slot text
    phan_mon_name_by_id: dict[str, str] = {}
    for pm in out.a_phan_mon:
        pid = str(pm.get("id", "")).strip()
        ten = str(pm.get("ten", "") or pm.get("sten", "") or "").strip()
        if pid and ten:
            phan_mon_name_by_id[pid] = ten

    mon_name_by_id: dict[str, str] = {}
    for pm in out.a_phan_mon:
        # aPhanMon mỗi record có cả ten_mon — giúp resolve mon_hoc_id → tên môn
        mon_id = str(pm.get("mon_hoc_id", "")).strip()
        ten_mon = str(pm.get("ten_mon", "") or "").strip()
        if mon_id and ten_mon and mon_id not in mon_name_by_id:
            mon_name_by_id[mon_id] = ten_mon
    for m_ in out.a_mon_hoc:
        mid = str(m_.get("id", "")).strip()
        mten = str(m_.get("ten", "") or m_.get("ten_mon", "") or "").strip()
        if mid and mten:
            mon_name_by_id[mid] = mten

    # 2. Parse table
    parser = _TableExtractor()
    try:
        parser.feed(html)
    except Exception as e:
        out.parse_warnings.append(f"html parse error: {type(e).__name__}: {e}")

    # 3. Build slot data — duyệt qua tất cả cboLopHoc selects
    by_row_key: dict[str, SlotData] = {}
    for sel_id, sel in parser.selects.items():
        m = _RE_ROW_KEY.match(sel_id)
        if not m:
            continue
        thu, buoi_idx, tiet_idx = int(m.group(1)), int(m.group(2)), int(m.group(3))
        row_key = f"{thu}_{buoi_idx}_{tiet_idx}"

        slot = SlotData(row_key=row_key, thu=thu, buoi_idx=buoi_idx, tiet_idx=tiet_idx)
        if sel.get("selected"):
            slot.lop_id = sel["selected"].get("value", "")
            slot.lop_text = sel["selected"].get("text", "")

        # Find khoi from selected option
        for opt in sel.get("options", []):
            if opt.get("value") == slot.lop_id and "khoi" in opt:
                slot.khoi = opt.get("khoi", "")
                break
        by_row_key[row_key] = slot

    # 4. Enrich từ <tr data_*>
    for tr in parser.tr_rows:
        stt = tr.get("data_stt", "")
        if not stt:
            continue
        slot = by_row_key.get(stt)
        if not slot:
            # Tạo slot rỗng nếu tr có data_stt nhưng không có cboLopHoc
            try:
                t, b, ti = stt.split("_")
                slot = SlotData(row_key=stt, thu=int(t), buoi_idx=int(b), tiet_idx=int(ti))
                by_row_key[stt] = slot
            except ValueError:
                continue

        # Fill từ tr attrs
        slot.lop_id = slot.lop_id or tr.get("data_lop_hoc_id", "")
        slot.mon_id = tr.get("data_mon_hoc_id", "") or slot.mon_id
        slot.phan_mon_id = tr.get("data_phan_mon_id", "")
        slot.ppct = tr.get("data_tiet_ppct", "")
        slot.id_giao_an = tr.get("id_giao_an", "")
        slot.khoi = tr.get("data_khoi_hoc_id", "") or slot.khoi

    # 5. Enrich từ các select khác và textarea/input
    for row_key, slot in by_row_key.items():
        # Môn học
        mon_sel = parser.selects.get(f"cboMonHoc_{row_key}")
        if mon_sel:
            slot.mon_dmonid = mon_sel.get("dmonid", "")
            slot.mon_options = [{"value": o.get("value", ""), "text": o.get("text", "")}
                               for o in mon_sel.get("options", [])]
            if mon_sel.get("selected"):
                slot.mon_id = mon_sel["selected"].get("value", slot.mon_id)
                # Lấy text từ option đang selected
                slot.mon_text = mon_sel["selected"].get("text", "")
            else:
                # Tìm option nào có value == mon_id thì lấy text
                for opt in mon_sel.get("options", []):
                    if opt.get("value") == slot.mon_id and opt.get("text"):
                        slot.mon_text = opt["text"]
                        break

        # Enrich mon_text từ inline aPhanMon nếu vẫn trống/số
        if slot.mon_id and slot.mon_id != "0" and (
            not slot.mon_text
            or slot.mon_text == "---"
            or slot.mon_text == slot.mon_id
            or slot.mon_text.isdigit()
        ):
            name = mon_name_by_id.get(slot.mon_id)
            if name:
                slot.mon_text = name

        # Phân môn
        pm_sel = parser.selects.get(f"cboPhanMon_{row_key}")
        if pm_sel:
            slot.phan_mon_options = [{"value": o.get("value", ""), "text": o.get("text", "")}
                                    for o in pm_sel.get("options", [])]
            if pm_sel.get("selected"):
                slot.phan_mon_id = pm_sel["selected"].get("value", slot.phan_mon_id)
                slot.phan_mon_text = pm_sel["selected"].get("text", "")

        # Enrich phan_mon_text từ aPhanMon (cho slot không có select option matching)
        if slot.phan_mon_id and slot.phan_mon_id != "0" and (
            not slot.phan_mon_text
            or slot.phan_mon_text == "---"
            or slot.phan_mon_text == slot.phan_mon_id
            or slot.phan_mon_text.isdigit()
        ):
            name = phan_mon_name_by_id.get(slot.phan_mon_id)
            if name:
                slot.phan_mon_text = name

        # Trạng thái
        tt_sel = parser.selects.get(f"cboTrangThai_{row_key}")
        if tt_sel and tt_sel.get("selected"):
            slot.trang_thai = tt_sel["selected"].get("value", "")
            slot.trang_thai_text = tt_sel["selected"].get("text", "")

        # PPCT input
        ppct_inp = parser.inputs.get(f"txtTietPPCT_{row_key}")
        if ppct_inp:
            slot.ppct = ppct_inp.get("value", "") or slot.ppct

        # Tiết TKB
        tkb_inp = parser.inputs.get(f"txtTietTKB_{row_key}")
        if tkb_inp:
            slot.tiet_tkb = tkb_inp.get("value", "")

        # Tên bài + Ghi chú (textarea)
        slot.ten_bai = parser.textareas.get(f"txtTenBai_{row_key}", "") or slot.ten_bai
        slot.ghi_chu = parser.textareas.get(f"txtGhiChu_{row_key}", "")

    # 6. Sort slots theo (thu, buoi, tiet)
    out.slots = sorted(by_row_key.values(),
                       key=lambda s: (s.thu, s.buoi_idx, s.tiet_idx))
    return out


# ---------------------------------------------------------------
# Canary check (#2) — xác minh cấu trúc web còn parse được TRƯỚC khi RUN
# ---------------------------------------------------------------

def assess_week_structure(wd: "WeekData") -> tuple[bool, list[str]]:
    """Đánh giá 1 WeekData (tuần mẫu) xem cấu trúc web có còn parse đúng.

    Tool tự động hóa VnEdu qua endpoint/UI nội bộ KHÔNG có hợp đồng. Nếu
    VnEdu đổi cấu trúc HTML/JSON, `parse_load_response` sẽ trả dữ liệu rỗng
    hoặc warning mà RUN hàng loạt không phát hiện kịp → ghi sai 35 tuần.
    Canary đọc 1 tuần mẫu rồi kiểm tra các tín hiệu cấu trúc cốt lõi.

    Tín hiệu kiểm tra:
      1. Không có parse_warnings (JSON inline parse sạch).
      2. Có ít nhất 1 slot được parse (bảng <tr data_stt> render đúng).
      3. aPhanMon (inline array) không rỗng — nguồn enrich tên môn/phân môn.

    Lưu ý: hàm thuần (không chạm web) để test được. Caller chịu trách
    nhiệm fetch tuần mẫu rồi truyền WeekData vào.

    Returns:
        (ok, problems). ok=True khi mọi tín hiệu đạt. problems liệt kê
        từng vấn đề (tiếng Việt) để cảnh báo user.
    """
    problems: list[str] = []
    if wd is None:
        return False, ["Không đọc được tuần mẫu (WeekData rỗng)."]

    if wd.parse_warnings:
        for w in wd.parse_warnings[:3]:
            problems.append(f"Lỗi đọc dữ liệu web: {w}")

    if not wd.slots:
        problems.append(
            "Không tách được ô tiết nào từ bảng web — có thể VnEdu đã đổi "
            "cấu trúc HTML hoặc tuần mẫu trống."
        )

    if not wd.a_phan_mon:
        problems.append(
            "Không đọc được danh sách phân môn (aPhanMon) — có thể VnEdu đã "
            "đổi cách nhúng dữ liệu, tên môn/phân môn sẽ thiếu."
        )

    return (not problems), problems

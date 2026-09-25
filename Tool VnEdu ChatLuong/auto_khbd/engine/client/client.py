"""KHDHClient: giao tiếp với trang KHDH qua Playwright."""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable

from ...log import logger
from ..parser import parse_load_response, WeekData
from .js import (
    _JS_CLICK_SAVE,
    _JS_DISMISS_DIALOGS,
    _JS_FETCH,
    _JS_FETCH_WEEKS_BATCH,
    _JS_GET_CONTEXT,
    _JS_GET_DATE_RANGE,
    _JS_GET_TOTAL_WEEKS,
    _JS_INSTALL_AUTOFILL_BLOCKER,
    _JS_INSTALL_SNIFFER,
    _JS_RELOAD_TABLE,
    _JS_SELECT_WEEK,
    _JS_SET_EDIT,
    _JS_SET_GOI_Y_TKB,
)
from .models import ApiResult, KHDHContext, SaveError, SaveResult


# ---------------------------------------------------------------
# KHDHClient
# ---------------------------------------------------------------

class KHDHClient:
    """Wrap mọi API endpoint VnEdu KHDH.

    Sử dụng:
        client = KHDHClient(page)
        ctx = client.fetch_context()
        wd = client.fetch_week(35)
        save_res = client.save_week_payload(35, payload)
    """

    def __init__(self, page, default_timeout_ms: int = 30000):
        self.page = page
        self.default_timeout_ms = default_timeout_ms
        self._context: KHDHContext | None = None
        # Apply default timeout cho mọi page operation (navigation, evaluate
        # internal navigation, wait_for_*). Trước đây giá trị này được lưu
        # nhưng KHÔNG truyền vào page → Playwright dùng default 30s cố định.
        try:
            page.set_default_timeout(default_timeout_ms)
        except Exception as e:
            logger.warning(f"set_default_timeout failed: {e}")
        # Inject sniffer ngay (idempotent)
        try:
            page.evaluate(_JS_INSTALL_SNIFFER)
        except Exception as e:
            logger.warning(f"install sniffer failed: {e}")
        # v3.2: Inject XHR autofill blocker — patch XMLHttpRequest để
        # tool có thể tạm block các request `getByTiet`/`getSuggestTietPpct`
        # của web khi đang nhập, tránh response async override PPCT đã set.
        # Patch idempotent — gọi lại không hại gì.
        try:
            page.evaluate(_JS_INSTALL_AUTOFILL_BLOCKER)
        except Exception as e:
            logger.warning(f"install autofill blocker failed: {e}")

    # -----------------------------------------------------------
    # CONTEXT
    # -----------------------------------------------------------

    def fetch_context(self, force: bool = False) -> KHDHContext:
        """Đọc context (token, user_id, gv_id, win_id, ...) từ trang đang mở.

        Retry tới 5 lần với backoff 2s nếu my_token chưa có (ExtJS chưa load,
        XHR chưa fire). Tổng max ~10s thay vì raise ngay (C4 fix).
        """
        if self._context and not force:
            return self._context

        # Retry loop: thử tối đa 5 lần với backoff
        max_attempts = 5
        backoff_s = 2.0
        raw: dict = {}
        for attempt in range(1, max_attempts + 1):
            try:
                raw = self.page.evaluate(_JS_GET_CONTEXT) or {}
            except Exception as e:
                logger.warning(f"fetch_context attempt {attempt}: evaluate failed: {e}")
                raw = {}

            if raw.get("my_token"):
                break  # Đã có token, thoát loop

            # Trên attempt 1, click Refresh để force XHR
            if attempt == 1:
                try:
                    self.page.evaluate(_JS_RELOAD_TABLE)
                except Exception:
                    pass

            # Sleep trước attempt sau (không sleep ở attempt cuối)
            if attempt < max_attempts:
                time.sleep(backoff_s)

        ctx = KHDHContext(
            my_token=str(raw.get("my_token") or ""),
            my_user_id=str(raw.get("my_user_id") or ""),
            nam_hoc=int(raw.get("nam_hoc") or 0),
            cap_hoc=int(raw.get("cap_hoc") or 0),
            cap_hoc_text=str(raw.get("cap_hoc_text") or ""),
            giao_vien_id=int(raw.get("giao_vien_id") or 0),
            giao_vien_name=str(raw.get("giao_vien_name") or ""),
            win_id=str(raw.get("win_id") or ""),
            ngay_tac_dung_tkb=str(raw.get("ngay_tac_dung_tkb") or ""),
            site_id=str(raw.get("site_id") or ""),
        )
        if not ctx.my_token:
            raise RuntimeError(
                f"Không lấy được my_token sau {max_attempts} lần thử "
                f"({max_attempts * backoff_s:.0f}s) — "
                "chưa đăng nhập VnEdu hoặc bảng KHDH chưa load"
            )
        if not ctx.giao_vien_id:
            raise RuntimeError("Không lấy được giao_vien_id — đảm bảo đang ở module KHDH")
        self._context = ctx
        return ctx

    def invalidate_context(self) -> None:
        """Xóa cache context — buộc fetch_context() refetch ở lần gọi tiếp.

        Dùng khi nghi ngờ token/session đã expire (vd response 403, hoặc
        success=false từ server). Giúp tránh tình trạng cache stale gây
        confusing error downstream (C5 fix).
        """
        self._context = None

    # -----------------------------------------------------------
    # MODE / WEEK SWITCHING
    # -----------------------------------------------------------

    def enable_goi_y_tkb_mode(self) -> bool:
        """Bật chế độ 'Sửa (Gợi ý theo TKB)' để load đầy đủ slot từ TKB.

        Dùng cho Bootstrap (lấy danh sách lớp/môn/phân môn) và Advanced
        (xem TKB pattern). KHÔNG dùng khi save vì web yêu cầu mode 'Sửa'.
        """
        r = self.page.evaluate(_JS_SET_GOI_Y_TKB)
        return bool(r and r.get("ok"))

    def enable_edit_mode(self) -> bool:
        """Bật chế độ 'Sửa' (không có gợi ý TKB).

        Đây là mode chuẩn để **save** dữ liệu. Mode 'Sửa (Gợi ý)' thường
        bị web khóa save vì nó hiển thị slot suggested chưa thực sự được
        chọn.
        """
        r = self.page.evaluate(_JS_SET_EDIT)
        return bool(r and r.get("ok"))

    def select_week_in_ui(self, tuan: int) -> bool:
        """Đổi tuần đang chọn trên combobox UI (sẽ trigger reload bảng)."""
        r = self.page.evaluate(_JS_SELECT_WEEK, tuan)
        return bool(r and r.get("ok"))

    def reload_table(self):
        """Click Refresh để reload bảng KHDH."""
        return self.page.evaluate(_JS_RELOAD_TABLE)

    def reload_table_and_wait(
        self, floor_s: float = 1.5, max_extra_s: float = 6.0,
        stop_event: "threading.Event | None" = None,
    ) -> bool:
        """(#3) Reload bảng + chờ DOM render xong, thay cho sleep cứng.

        No-downside so với `reload_table() + time.sleep(floor_s)`:
          - VẪN chờ tối thiểu `floor_s` (giữ nguyên hành vi đã chứng minh
            chạy được — không proceed sớm hơn hiện trạng).
          - Sau floor, NẾU bảng chưa render (không có ô cboLopHoc nào),
            chờ THÊM tối đa `max_extra_s` đến khi render — giải quyết case
            mạng trường học chậm khiến 1.5s không đủ → trước đây ghi khi
            DOM chưa sẵn sàng → mất tiết.

        Returns:
            True nếu bảng đã render (hoặc floor đã đủ). False nếu hết
            max_extra mà vẫn chưa thấy ô (caller vẫn proceed — verify-after-
            save sẽ là lưới an toàn cuối).
        """
        try:
            self.reload_table()
        except Exception as e:
            logger.warning(f"reload_table_and_wait: reload failed: {e}")
        # Floor: chờ tối thiểu (interruptible)
        slept = 0.0
        while slept < floor_s:
            if stop_event is not None and stop_event.is_set():
                return False
            step = min(0.1, floor_s - slept)
            time.sleep(step)
            slept += step
        # Sau floor: poll DOM ready, chờ thêm tối đa max_extra_s
        deadline = time.monotonic() + max_extra_s
        while time.monotonic() < deadline:
            if stop_event is not None and stop_event.is_set():
                return False
            try:
                ready = self.page.evaluate(
                    "() => !!document.querySelector('[id^=\"cboLopHoc_\"]')"
                )
                if ready:
                    return True
            except Exception:
                pass
            time.sleep(0.2)
        return False

    # -----------------------------------------------------------
    # CORE FETCH/POST
    # -----------------------------------------------------------

    def _do_fetch(
        self,
        url: str,
        method: str = "POST",
        body: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> ApiResult:
        """Fetch qua page.evaluate. Trả ApiResult với raw text."""
        req = {
            "url": url,
            "method": method,
            "body": body,
            "headers": headers or {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
        }
        try:
            res = self.page.evaluate(_JS_FETCH, req)
        except Exception as e:
            return ApiResult(ok=False, error=f"page.evaluate: {type(e).__name__}: {e}")
        if not res or not res.get("ok"):
            return ApiResult(ok=False, error=res.get("error", "fetch_failed"),
                            duration_ms=int(res.get("duration_ms", 0) if res else 0))
        status = int(res.get("status") or 0)
        # Auth invalidation: nếu server trả 401/403 → cache context có thể
        # đã stale (token expired, user logged out). Clear cache để lần fetch
        # tiếp theo refetch context fresh từ ExtJS (C5 fix).
        if status in (401, 403):
            logger.warning(f"_do_fetch: status={status} → invalidate context cache")
            self.invalidate_context()
        return ApiResult(ok=True, status=status,
                        raw=res.get("text") or "",
                        duration_ms=int(res.get("duration_ms") or 0))

    @staticmethod
    def _build_form_body(params: dict[str, Any]) -> str:
        """Encode dict thành application/x-www-form-urlencoded (giữ thứ tự, escape)."""
        from urllib.parse import quote
        parts = []
        for k, v in params.items():
            if v is None:
                v = ""
            elif isinstance(v, bool):
                v = "1" if v else "0"
            else:
                v = str(v)
            parts.append(f"{quote(str(k), safe='')}={quote(v, safe='')}")
        return "&".join(parts)

    # -----------------------------------------------------------
    # ENDPOINT WRAPPERS
    # -----------------------------------------------------------

    def fetch_week(self, tuan: int, is_edit: int = 2) -> WeekData:
        """[#1] Đọc 1 tuần KHDH.

        is_edit: 1=Sửa, 2=Sửa (Gợi ý theo TKB)

        Returns: WeekData
        Raises: RuntimeError nếu API fail.
        """
        ctx = self.fetch_context()
        url = f"?load=edu.lich_bao_giang.lich_bao_giang&app_nam_hoc={ctx.nam_hoc}&my_token={ctx.my_token}"
        params = {
            "my_token": ctx.my_token,
            "my_user_id": ctx.my_user_id,
            "app_nam_hoc": ctx.nam_hoc,
            "winId": ctx.win_id,
            "namHoc": ctx.nam_hoc,
            "capHoc": ctx.cap_hoc,
            "giaoVienId": ctx.giao_vien_id,
            "tuanHoc": tuan,
            "iNgayTacDung": ctx.ngay_tac_dung_tkb,
            "isEdit": is_edit,
        }
        body = self._build_form_body(params)
        res = self._do_fetch(url, method="POST", body=body)
        if not res.ok or res.status != 200:
            raise RuntimeError(f"fetch_week({tuan}) failed: status={res.status} err={res.error}")
        return parse_load_response(res.raw, tuan)

    def fetch_weeks_parallel(
        self,
        tuans: list[int],
        is_edit: int = 1,
        batch_size: int = 6,
        on_batch: Callable[[int, int, list[int]], None] | None = None,
        stop_event: threading.Event | None = None,
    ) -> dict[int, WeekData]:
        """Fetch nhiều tuần SONG SONG qua `Promise.all` trong 1 page.evaluate.

        Cải tiến đáng kể so với fetch tuần tự: test thực tế cho thấy
        6 tuần/batch mất ~1.5s thay vì ~6s tuần tự. Server VnEdu xử lý
        nhiều request `lich_bao_giang` song song được.

        Args:
            tuans: list các số tuần cần fetch.
            is_edit: 1=Sửa, 2=Sửa (Gợi ý theo TKB). Mặc định 1 cho detect.
            batch_size: số tuần mỗi batch. 6 là sweet spot — đủ nhanh nhưng
                không quá tải server (mỗi response ~180KB).
            on_batch: callback(done_count, total, batch_weeks) sau mỗi batch.
            stop_event: cho phép user dừng giữa chừng.

        Returns:
            dict {week_num: WeekData}. Tuần fail bị skip (không có trong dict).
        """
        ctx = self.fetch_context()
        ctx_dict = {
            "my_token": ctx.my_token,
            "my_user_id": ctx.my_user_id,
            "nam_hoc": ctx.nam_hoc,
            "cap_hoc": ctx.cap_hoc,
            "giao_vien_id": ctx.giao_vien_id,
            "win_id": ctx.win_id,
            "ngay_tac_dung": ctx.ngay_tac_dung_tkb,
        }

        out: dict[int, WeekData] = {}
        total = len(tuans)
        done = 0
        for i in range(0, total, batch_size):
            if stop_event is not None and stop_event.is_set():
                break
            batch = tuans[i:i + batch_size]
            try:
                resp = self.page.evaluate(
                    _JS_FETCH_WEEKS_BATCH,
                    {"ctx": ctx_dict, "weeks": batch, "is_edit": is_edit},
                )
            except Exception as e:
                logger.warning(f"fetch_weeks_parallel batch {batch} failed: {e}")
                done += len(batch)
                if on_batch:
                    on_batch(done, total, batch)
                continue
            if not resp or not resp.get("ok"):
                err = resp.get("err", "fetch_failed") if resp else "no_response"
                logger.warning(f"fetch_weeks_parallel batch {batch} err: {err}")
                done += len(batch)
                if on_batch:
                    on_batch(done, total, batch)
                continue
            for r in resp.get("results", []):
                w = int(r.get("week") or 0)
                if not r.get("ok"):
                    continue
                text = r.get("text") or ""
                if not text:
                    continue
                try:
                    wd = parse_load_response(text, w)
                    out[w] = wd
                except Exception as parse_e:
                    logger.warning(f"parse week {w}: {parse_e}")
            done += len(batch)
            if on_batch:
                on_batch(done, total, batch)
        return out

    def get_total_weeks(self) -> dict:
        """Đọc tổng số tuần từ combobox `cboTuanHoc` trên web.

        Returns:
            {ok: bool, total: int, min: int, max: int, current: int} hoặc
            {ok: False, err: str}.
        """
        try:
            return self.page.evaluate(_JS_GET_TOTAL_WEEKS) or {"ok": False}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def save_week_payload(self, tuan: int, fields: dict[str, str], tu_ngay: str = "", den_ngay: str = "") -> SaveResult:
        """[#2] Lưu 1 tuần với payload tự build.

        Args:
            tuan: số tuần
            fields: dict các field {field_id: value} cần override (vd
                {"txtTenBai_2_2_1": "Unit X", "txtTietPPCT_2_2_1": "103"}).
                Các field không truyền sẽ giữ nguyên giá trị hiện tại.
            tu_ngay, den_ngay: dạng dd/MM/yyyy. Nếu rỗng thì đọc từ form hiện tại.

        Lưu ý: Endpoint này yêu cầu **toàn bộ form 70 row × 8 field**.
        Nếu chỉ truyền 1 field, server sẽ lưu mỗi field đó nhưng các slot khác
        có thể bị reset. → Khuyến nghị: dùng `save_week_via_button()` để click Lưu
        trên UI sau khi đã set values vào DOM.

        Returns: SaveResult
        """
        ctx = self.fetch_context()
        # Đọc tu_ngay / den_ngay từ form nếu chưa truyền
        if not tu_ngay or not den_ngay:
            r = self.page.evaluate(_JS_GET_DATE_RANGE)
            if r and r.get("ok"):
                tu_ngay = tu_ngay or r.get("tuNgay") or ""
                den_ngay = den_ngay or r.get("denNgay") or ""

        params: dict[str, Any] = {
            "iNamHoc": ctx.nam_hoc,
            "capHoc": ctx.cap_hoc,
            "iGiaoVienId": ctx.giao_vien_id,
            "iTuanHoc": tuan,
            "tuNgay": tu_ngay,
            "denNgay": den_ngay,
        }
        params.update(fields)
        body = self._build_form_body(params)
        url = f"?call=edu.lich_bao_giang.save&app_nam_hoc={ctx.nam_hoc}&my_token={ctx.my_token}"
        res = self._do_fetch(url, method="POST", body=body)
        return self._parse_save_response(res)

    def save_week_via_button(self, wait_s: float = 10.0) -> SaveResult:
        """[#2 alternative] Click nút Lưu trên UI và chờ XHR save quay về.

        Cách này an toàn nhất vì web tự serialize form đầy đủ, không sợ thiếu field.
        Caller phải đảm bảo trước đó đã set values vào DOM (qua DOM manipulation).

        Returns: SaveResult — đọc raw body từ XHR sniffer (đã capture full response)
        """
        self.fetch_context()
        # Đóng các dialog tiền nhiệm (vd "Chưa chọn lớp báo giảng?" do
        # set cboTrangThai trước đó) để không che nút Lưu.
        try:
            self.dismiss_dialogs()
        except Exception:
            pass
        # Clear save XHRs cũ
        self.page.evaluate(
            "() => { if (window.__xhrLog) { window.__xhrLog = window.__xhrLog.filter(x => !/lich_bao_giang.save/.test(x.url || '')); } return true; }"
        )
        # Click Save
        r = self.page.evaluate(_JS_CLICK_SAVE)
        if not r or not r.get("ok"):
            return SaveResult(ok=False, success=False,
                            msg=f"click_save_failed: {r and r.get('err')}")
        # Wait for save XHR to complete. Có thể nhiều entry trong log do
        # sniffer cũ còn patch (v1 không có response_excerpt). Phải lấy
        # entry mới nhất CÓ response_excerpt thực sự (entry v2 đầy đủ).
        deadline = time.monotonic() + wait_s
        last_save_xhr = None
        while time.monotonic() < deadline:
            time.sleep(0.3)
            log = self.page.evaluate(
                "() => (window.__xhrLog || []).filter(x => /lich_bao_giang.save/.test(x.url || ''))"
            )
            # Tìm entry MỚI NHẤT có response_excerpt
            best = None
            for x in reversed(log):
                if x.get("response_excerpt"):
                    best = x
                    break
            if best:
                last_save_xhr = best
                # Đợi thêm chút để chắc response đã capture đầy đủ
                time.sleep(0.4)
                log2 = self.page.evaluate(
                    "() => (window.__xhrLog || []).filter(x => /lich_bao_giang.save/.test(x.url || ''))"
                )
                best2 = None
                for x in reversed(log2):
                    if x.get("response_excerpt"):
                        best2 = x
                        break
                if best2:
                    last_save_xhr = best2
                break
            elif log:
                # Có XHR save nhưng chưa có response_excerpt — chờ tiếp
                continue

        # Đóng dialog "Đã lưu thành công!" / "Còn tồn tại các ô chưa nhập đủ"
        # để các thao tác kế tiếp không bị che. Đợi 0.5s cho dialog hiện ra
        # rồi mới đóng (vì handler success/fail chạy async sau XHR).
        time.sleep(0.5)
        try:
            self.dismiss_dialogs()
        except Exception:
            pass

        if not last_save_xhr:
            return SaveResult(ok=False, success=False,
                            msg="Hết thời gian chờ phản hồi từ web. "
                                "Có thể web bị nghẽn — kiểm tra lại trên trình duyệt.")

        status = int(last_save_xhr.get("status") or 0)
        resp_text = last_save_xhr.get("response_excerpt") or ""

        # Build pseudo ApiResult để dùng _parse_save_response
        ar = ApiResult(ok=True, status=status, raw=resp_text,
                       duration_ms=int(last_save_xhr.get("duration_ms") or 0))
        if not resp_text:
            # FAIL-SAFE: không có response body = không xác minh được kết quả.
            # Trả ok=False để halt-on-error trigger, an toàn hơn coi như success.
            return SaveResult(
                ok=False, success=False,
                msg=f"Web trả status={status} nhưng không đọc được nội dung "
                    f"phản hồi. Để an toàn, tool dừng để bạn kiểm tra lại "
                    f"trên web xem tuần này đã lưu chưa.",
            )
        return self._parse_save_response(ar)

    def dismiss_dialogs(self) -> dict:
        """Đóng các dialog Ext.MessageBox / popup nhỏ đang hiện.

        Trả dict {ok, closed: list[{title, msg}]} — caller dùng để biết
        đã đóng dialog gì (vd dialog "Chưa chọn lớp" cảnh báo benign).
        """
        try:
            return self.page.evaluate(_JS_DISMISS_DIALOGS)
        except Exception as e:
            return {"ok": False, "err": f"{type(e).__name__}: {e}"}

    def get_blocked_autofill_stats(self) -> dict:
        """Trả số XHR/fetch/script tag đã bị blocker abort.

        Returns:
            {ok, count, by_transport: {xhr: N, fetch: N, script: N},
             recent: [last 5 entries]}
        Hoặc {ok: False} nếu blocker chưa được install.
        """
        try:
            res = self.page.evaluate(r"""
() => {
    const log = window.__khdhBlockedAutofillLog;
    if (!Array.isArray(log)) return {ok: false, count: 0};
    const by = {xhr: 0, fetch: 0, script: 0};
    for (const e of log) {
        const t = String(e.transport || 'xhr');
        if (by[t] !== undefined) by[t]++;
        else by[t] = 1;
    }
    const recent = log.slice(-5).map(e => ({
        ts: e.ts, transport: e.transport,
        url: String(e.url || '').slice(0, 120),
    }));
    return {
        ok: true, count: log.length,
        by_transport: by, recent: recent,
        block_active: !!window.__khdhBlockAutofill,
    };
}
""")
            return res or {"ok": False}
        except Exception:
            return {"ok": False}

    def reset_blocked_autofill_log(self) -> None:
        """Xóa log XHR đã block — gọi đầu mỗi tuần để đếm lại."""
        try:
            self.page.evaluate(
                "() => { window.__khdhBlockedAutofillLog = []; }"
            )
        except Exception:
            pass

    @staticmethod
    def _parse_save_response(res: ApiResult) -> SaveResult:
        if not res.ok or res.status != 200:
            return SaveResult(ok=False, success=False, msg=res.error or f"status={res.status}",
                            duration_ms=res.duration_ms)
        try:
            data = json.loads(res.raw)
        except Exception as e:
            return SaveResult(ok=False, success=False, msg=f"json parse: {e}", duration_ms=res.duration_ms)
        success = bool(data.get("success"))
        errors_raw = data.get("error") or {}
        errors: list[SaveError] = []
        for fid, msg in errors_raw.items():
            # cboTrangThai_<rk> trả số (0/1/...) là status code, không phải lỗi
            if fid.startswith("cboTrangThai_"):
                continue
            # txtTenBai_ trống là warning, không phải lỗi nghiêm trọng —
            # giáo viên có thể tự bổ sung tên bài sau trên web.
            # Không coi là lỗi để tránh halt khi phân môn không có CSDL tên bài
            # (vd Hoạt động trải nghiệm, Sinh hoạt lớp...).
            if fid.startswith("txtTenBai_"):
                continue
            row_key = ""
            for prefix in ("txtTenBai_", "txtTietPPCT_", "cboLopHoc_", "cboMonHoc_",
                          "cboPhanMon_", "txtGhiChu_"):
                if fid.startswith(prefix):
                    row_key = fid[len(prefix):]
                    break
            errors.append(SaveError(field_id=fid, row_key=row_key, message=str(msg)))
        return SaveResult(
            ok=True, success=success, msg=str(data.get("msg") or ""),
            errors=errors, raw=data, duration_ms=res.duration_ms,
        )

    def gen_from_prev_week(self, tuan: int, tu_ngay: str = "", den_ngay: str = "") -> ApiResult:
        """[#3] Tạo KHDH tuần X từ tuần X-1 (web utility).

        Server sẽ copy slot từ tuần X-1 sang tuần X, tự increment PPCT.
        Đây là chức năng quan trọng nhất theo strategy bạn chọn.
        """
        ctx = self.fetch_context()
        if not tu_ngay or not den_ngay:
            r = self.page.evaluate(_JS_GET_DATE_RANGE)
            if r and r.get("ok"):
                tu_ngay = tu_ngay or r.get("tuNgay") or ""
                den_ngay = den_ngay or r.get("denNgay") or ""
        params = {
            "nam_hoc": ctx.nam_hoc,
            "cap_hoc": ctx.cap_hoc,
            "gv_id": ctx.giao_vien_id,
            "tuan": tuan,
            "tu_ngay": tu_ngay,
            "den_ngay": den_ngay,
        }
        url = f"?call=edu.lich_bao_giang.genLichBaoGiangTuan&app_nam_hoc={ctx.nam_hoc}&my_token={ctx.my_token}"
        body = self._build_form_body(params)
        res = self._do_fetch(url, method="POST", body=body)
        return self._wrap_json(res)

    def gen_from_tkb(self, tuan: int) -> ApiResult:
        """[#4] Tạo KHDH tuần X từ TKB (chưa có tên bài, PPCT trống)."""
        ctx = self.fetch_context()
        params = {
            "nam_hoc": ctx.nam_hoc,
            "cap_hoc": ctx.cap_hoc,
            "gv_id": ctx.giao_vien_id,
            "tuan": tuan,
        }
        url = f"?call=edu.lich_bao_giang.genLichBaoGiangTuanTheoTKB&app_nam_hoc={ctx.nam_hoc}&my_token={ctx.my_token}"
        body = self._build_form_body(params)
        res = self._do_fetch(url, method="POST", body=body)
        return self._wrap_json(res)

    def delete_year(self) -> ApiResult:
        """[#6] Xoá toàn bộ KHDH cấp hiện tại của giáo viên."""
        ctx = self.fetch_context()
        params = {"nam_hoc": ctx.nam_hoc, "cap_hoc": ctx.cap_hoc}
        url = f"?call=edu.lich_bao_giang.xoaLichBaoGiangGV&app_nam_hoc={ctx.nam_hoc}&my_token={ctx.my_token}"
        body = self._build_form_body(params)
        res = self._do_fetch(url, method="POST", body=body)
        return self._wrap_json(res)

    def delete_week(self, tuan: int) -> ApiResult:
        """[#7] Xoá KHDH 1 tuần của giáo viên."""
        ctx = self.fetch_context()
        params = {
            "nam_hoc": ctx.nam_hoc,
            "cap_hoc": ctx.cap_hoc,
            "gv_id": ctx.giao_vien_id,
            "tuan": tuan,
        }
        url = f"?call=edu.lich_bao_giang.xoaLichBaoGiangGVHienTai&app_nam_hoc={ctx.nam_hoc}&my_token={ctx.my_token}"
        body = self._build_form_body(params)
        res = self._do_fetch(url, method="POST", body=body)
        return self._wrap_json(res)

    def delete_weeks_range(
        self,
        tuan_from: int,
        tuan_to: int,
        on_progress: Callable[[int, int, int, ApiResult], None] | None = None,
        stop_event: "threading.Event | None" = None,
    ) -> dict:
        """[#7-batch] Xoá nhiều tuần liên tiếp của giáo viên hiện tại.

        SIẾT LOGIC quan trọng:
        - **CHỈ** dùng endpoint `xoaLichBaoGiangGVHienTai` (xóa 1 tuần cho
          GV) — TUYỆT ĐỐI KHÔNG dùng `xoaLichBaoGiangGV` (xóa cả cấp).
        - Halt-on-error: nếu 1 tuần fail (server reject hoặc network),
          DỪNG NGAY → tránh ảnh hưởng các tuần sau (có thể session expired).
        - Chỉ xóa các tuần liên tiếp từ `tuan_from` đến `tuan_to` (inclusive).
        - Validate `1 ≤ tuan_from ≤ tuan_to ≤ 52`.
        - `tuan` được truyền explicit qua body, KHÔNG đọc từ combobox web —
          tránh race khi user đổi tuần trên web giữa loop.

        Args:
            tuan_from: tuần bắt đầu (inclusive, ≥1)
            tuan_to: tuần kết thúc (inclusive, ≤52)
            on_progress: callback(idx, total, tuan, api_result) sau mỗi tuần
            stop_event: cho phép dừng giữa chừng

        Returns:
            dict với keys:
              - "ok": True nếu hoàn tất + không tuần nào fail
              - "deleted": list[int] các tuần đã xóa thành công
              - "failed": list[(tuan, msg)] tuần fail (max 1 do halt-on-error)
              - "stopped": True nếu user dừng giữa chừng
        """
        # Validate range — fail-fast trước khi gọi API
        if not isinstance(tuan_from, int) or not isinstance(tuan_to, int):
            raise ValueError(
                f"tuan_from và tuan_to phải là int, "
                f"nhận: {type(tuan_from).__name__}, {type(tuan_to).__name__}"
            )
        if not (1 <= tuan_from <= 52):
            raise ValueError(
                f"tuan_from = {tuan_from} ngoài khoảng 1..52"
            )
        if not (1 <= tuan_to <= 52):
            raise ValueError(
                f"tuan_to = {tuan_to} ngoài khoảng 1..52"
            )
        if tuan_from > tuan_to:
            raise ValueError(
                f"tuan_from ({tuan_from}) > tuan_to ({tuan_to}) — "
                "phải from ≤ to"
            )

        weeks = list(range(tuan_from, tuan_to + 1))
        deleted: list[int] = []
        failed: list[tuple[int, str]] = []
        stopped = False
        for i, t in enumerate(weeks, 1):
            if stop_event is not None and stop_event.is_set():
                stopped = True
                break
            try:
                res = self.delete_week(t)
            except Exception as e:
                failed.append((t, f"{type(e).__name__}: {e}"))
                if on_progress:
                    try:
                        on_progress(i, len(weeks), t, None)
                    except Exception:
                        pass
                break  # halt-on-error
            # delete_week trả ApiResult đã wrap JSON.
            # Server response: {"success": true, "msg": "...", "data": []}
            success = bool(
                res
                and res.ok
                and res.data
                and isinstance(res.data, dict)
                and res.data.get("success")
            )
            if on_progress:
                try:
                    on_progress(i, len(weeks), t, res)
                except Exception:
                    pass
            if success:
                deleted.append(t)
            else:
                msg = ""
                if res and res.data and isinstance(res.data, dict):
                    msg = str(res.data.get("msg") or "")
                if not msg and res:
                    msg = str(res.error or f"status={res.status}")
                failed.append((t, msg or "unknown error"))
                break  # halt-on-error
        return {
            "ok": len(failed) == 0 and not stopped,
            "deleted": deleted,
            "failed": failed,
            "stopped": stopped,
        }

    def get_log(self, tuan: int = 0, page_num: int = 1, limit: int = 100) -> ApiResult:
        """[#14] Lấy log slot bị xoá. tuan=0 → all weeks."""
        ctx = self.fetch_context()
        params = {
            "my_user_id": ctx.my_user_id,
            "app_nam_hoc": ctx.nam_hoc,
            "my_token": ctx.my_token,
            "capHoc": ctx.cap_hoc,
            "gv_id": ctx.giao_vien_id,
            "tuan": tuan,
            "page": page_num,
            "start": (page_num - 1) * limit,
            "limit": limit,
        }
        from urllib.parse import urlencode
        url = f"?call=edu.lich_bao_giang.getLichBaoGiangLog&{urlencode(params)}"
        res = self._do_fetch(url, method="GET", body=None)
        return self._wrap_json(res)

    def restore_log_records(self, records: list[dict]) -> ApiResult:
        """[#15] Khôi phục các slot từ log."""
        ctx = self.fetch_context()
        # API expect data[<idx>][<field>]=value — dạng PHP-style array
        payload: dict[str, Any] = {}
        for i, rec in enumerate(records):
            for k, v in rec.items():
                payload[f"data[{i}][{k}]"] = v
        url = f"?call=edu.lich_bao_giang.khoiPhuc&app_nam_hoc={ctx.nam_hoc}&my_token={ctx.my_token}"
        body = self._build_form_body(payload)
        res = self._do_fetch(url, method="POST", body=body)
        return self._wrap_json(res)

    @staticmethod
    def _wrap_json(res: ApiResult) -> ApiResult:
        """Parse JSON response; nếu fail thì giữ raw."""
        if not res.ok:
            return res
        try:
            res.data = json.loads(res.raw)
        except Exception:
            res.data = None
        return res

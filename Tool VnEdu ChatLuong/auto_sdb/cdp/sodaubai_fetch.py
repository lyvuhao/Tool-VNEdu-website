"""Lấy dữ liệu sổ đầu bài (từng tuần và hàng loạt) qua service nội bộ của VnEdu.

Phần JavaScript chạy trong trang nằm ở `sodaubai_fetch_js.py`.
"""

import copy

from ..compat import PlaywrightTimeout
from .sodaubai_fetch_js import JS_FETCH_SODAUBAI_BULK, JS_FETCH_SODAUBAI_WEEK


def normalize_week_numbers(tuan_nums):
    """Số tuần hợp lệ (nguyên, >= 1), bỏ trùng, giữ thứ tự."""
    week_numbers = []
    seen = set()
    for item in list(tuan_nums or []):
        try:
            week_num = int(item)
        except Exception:
            continue
        if week_num < 1 or week_num in seen:
            continue
        seen.add(week_num)
        week_numbers.append(week_num)
    return week_numbers


class SoDauBaiFetchMixin:
    """Lấy dữ liệu sổ đầu bài (từng tuần và hàng loạt)."""

    def fetch_sodaubai_rows(self, lop_text, tuan_num, timeout_s=12.0, class_meta=None,
                            show_goi_y=False):
        """Fetch trực tiếp HTML sổ đầu bài theo lớp/tuần rồi parse ra rows.

        Đi đường service nội bộ của VnEdu để tránh tình trạng UI dropdown đã đổi
        nhưng grid chưa refresh kịp.

        Returns:
            (success, payload|message)
            payload = {
                "rows": list[dict],
                "week": int|None,
                "fetch_ms": int,
                "html_length": int,
                "lop": str,
            }
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        timeout_ms = max(int(timeout_s * 1000), 3000)
        try:
            result = self.page.evaluate(
                JS_FETCH_SODAUBAI_WEEK,
                {
                    "lopText": str(lop_text),
                    "tuanNum": int(tuan_num),
                    "timeoutMs": int(timeout_ms),
                    "classMeta": copy.deepcopy(class_meta) if class_meta else {},
                    "showGoiY": bool(show_goi_y),
                },
            )
            if result.get("ok"):
                return True, result
            return False, result.get("error", "Fetch sổ đầu bài thất bại")
        except PlaywrightTimeout:
            return False, f"Timeout fetch sổ đầu bài cho lớp {lop_text}, tuần {tuan_num}"
        except Exception as e:
            return False, f"Lỗi fetch sổ đầu bài: {type(e).__name__}: {str(e)[:120]}"

    def fetch_sodaubai_rows_bulk(self, lop_text, tuan_nums, timeout_s=24.0, concurrency=6,
                                 class_meta=None, show_goi_y=False):
        """Fetch nhiều tuần sổ đầu bài trong một lần evaluate để giảm roundtrip.

        Returns:
            (success, payload|message)
            payload = {
                "results": [
                    {
                        "requested_week": int,
                        "ok": bool,
                        "payload": dict,   # nếu ok
                        "error": str,      # nếu fail
                    }
                ],
                "concurrency": int,
                "requested_count": int,
            }
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        week_numbers = normalize_week_numbers(tuan_nums)
        if not week_numbers:
            return True, {"results": [], "concurrency": 0, "requested_count": 0}

        try:
            result = self.page.evaluate(
                JS_FETCH_SODAUBAI_BULK,
                {
                    "lopText": str(lop_text),
                    "tuanNums": week_numbers,
                    "timeoutMs": max(int(timeout_s * 1000), 3000),
                    "concurrency": max(1, min(int(concurrency or 6), 8)),
                    "classMeta": copy.deepcopy(class_meta) if class_meta else {},
                    "showGoiY": bool(show_goi_y),
                },
            )
            if result.get("ok"):
                return True, result
            return False, result.get("error", "Bulk fetch sổ đầu bài thất bại")
        except PlaywrightTimeout:
            return False, f"Timeout bulk fetch sổ đầu bài cho lớp {lop_text}"
        except Exception as e:
            return False, f"Lỗi bulk fetch sổ đầu bài: {type(e).__name__}: {str(e)[:120]}"

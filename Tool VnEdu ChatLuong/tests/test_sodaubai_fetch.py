"""fetch_sodaubai_rows(_bulk) (auto_sdb/cdp/sodaubai_fetch.py) + JS dùng chung (sodaubai_fetch_js.py).

- Phần không cần trình duyệt: ghép JS, chuẩn hoá danh sách tuần.
- Phần có trình duyệt: trang VnEdu giả (Ext combobox Lớp/Cấp, token) + service trả HTML sổ đầu bài,
  chạy bằng Chromium của Playwright. Tự bỏ qua nếu chưa cài; chỉ định Chromium qua VNEDU_TEST_CHROMIUM.
"""

import os
import re
import sys
import unittest
from urllib.parse import parse_qs

TOOL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOL_DIR)

from auto_sdb.cdp import sodaubai_fetch_js as J  # noqa: E402
from auto_sdb.cdp.sodaubai_fetch import SoDauBaiFetchMixin, normalize_week_numbers  # noqa: E402

BASE = "http://sdb.test/"

FAKE_PAGE_SETUP = '''(cfg) => {
    window.myToken = () => 'tok';
    window.myUserId = 'u1';
    window.phpviet_nam_hoc_v5 = 2025;
    const lop = {name: 'cboLopHoc', displayField: 'ten', valueField: 'id', store: {data: {items: [
        {get: (f) => ({ten: ' 6A1 ', id: '123', khoi: '', cap: ''})[f]},
    ]}}};
    const combos = [lop];
    if (cfg.cap) combos.push({getName: () => 'cboCapHoc', getValue: () => cfg.cap});
    window.Ext = {ComponentQuery: {query: (sel) => sel === 'combobox' ? combos : []}};
}'''


def week_html(week, reported_week=None):
    """Bảng một ngày (Thứ 2, buổi Sáng, 3 tiết): tiết 1 đã có dữ liệu, tiết 2 gợi ý KHDH, tiết 3 trống."""
    reported = week if reported_week is None else reported_week
    add = '<a class="add add_tiet_so_dau_bai">+</a>'
    return (
        f'<html><body><input type="hidden" name="iTuanHoc" value="{reported}">'
        '<table class="table">'
        '<tr><th>Thứ</th><th>Buổi</th><th></th><th>Tiết</th><th>Môn</th></tr>'
        '<tr>' + ''.join(f'<td>{i}</td>' for i in range(1, 13)) + '</tr>'
        '<tr><td rowspan="3">2<br>08/09/2025</td><td rowspan="3">Sáng</td>'
        '<td chitiet_id="77"></td><td>1</td><td>Toán</td><td>12</td><td></td><td>Bài 1</td></tr>'
        f'<tr><td mon_hoc_id="11" tiet_ppct="13">{add}</td><td>2</td><td>Toán</td><td></td><td></td>'
        '<td><span style="color:red">Bài 2</span></td></tr>'
        f'<tr><td>{add}</td><td>3</td><td></td></tr>'
        '</table></body></html>'
    )


class _Bridge(SoDauBaiFetchMixin):
    def __init__(self, page):
        self.page = page
        self.is_connected = True


class SoDauBaiFetchStaticTests(unittest.TestCase):
    def test_js_assembly(self):
        for script in (J.JS_FETCH_SODAUBAI_WEEK, J.JS_FETCH_SODAUBAI_BULK):
            self.assertTrue(script.startswith("async (args) => {"))
            self.assertTrue(script.rstrip().endswith("}"))
            self.assertEqual(script.count("{"), script.count("}"))
            for name in ("normalize", "getCombo", "textOf", "parseRowsFromTable", "resolveSdbClass",
                         "readSdbSession", "buildSdbParams", "parseWeekValue"):
                self.assertEqual(len(re.findall(r"function " + name + r"\(", script)), 1, name)
        self.assertIn("async function fetchOne(", J.JS_FETCH_SODAUBAI_BULK)

    def test_normalize_week_numbers(self):
        self.assertEqual(normalize_week_numbers([3, "2", 3, 0, -1, "x", None, 2.0, 5]), [3, 2, 5])
        self.assertEqual(normalize_week_numbers(None), [])

    def test_not_connected(self):
        bridge = _Bridge(None)
        bridge.is_connected = False
        self.assertEqual(bridge.fetch_sodaubai_rows("6A1", 1), (False, "Chưa kết nối CDP"))
        self.assertEqual(bridge.fetch_sodaubai_rows_bulk("6A1", [1]), (False, "Chưa kết nối CDP"))
        self.assertEqual(_Bridge(None).fetch_sodaubai_rows_bulk("6A1", ["x"]),
                         (True, {"results": [], "concurrency": 0, "requested_count": 0}))


class SoDauBaiFetchBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
            cls._pw = sync_playwright().start()
            executable = os.environ.get("VNEDU_TEST_CHROMIUM") or None
            cls._browser = cls._pw.chromium.launch(executable_path=executable, args=["--no-sandbox"])
        except Exception as error:  # noqa: BLE001 - không có Playwright/Chromium thì bỏ qua
            raise unittest.SkipTest(f"cần Playwright + Chromium: {error}")
        cls._page = cls._browser.new_page()
        cls.requests = []
        cls.responses = {}
        cls._page.route(BASE + "**", cls._handle)
        cls._page.goto(BASE)

    @classmethod
    def tearDownClass(cls):
        cls._browser.close()
        cls._pw.stop()

    @classmethod
    def _handle(cls, route):
        request = route.request
        if request.method != "POST":
            return route.fulfill(status=200, content_type="text/html", body="<html><body>VnEdu</body></html>")
        body = {k: v[0] for k, v in parse_qs(request.post_data or "", keep_blank_values=True).items()}
        cls.requests.append((request.url, body))
        week = int(body["tuanHoc"])
        kind = cls.responses.get(week, "ok")
        if kind == "http":
            return route.fulfill(status=500, body="lỗi")
        if kind == "abort":
            return route.abort()
        if kind == "wrong_week":
            return route.fulfill(status=200, content_type="text/html", body=week_html(week, week + 1))
        return route.fulfill(status=200, content_type="text/html", body=week_html(week))

    def setUp(self):
        type(self).requests = []
        type(self).responses = {}
        self._page.evaluate(FAKE_PAGE_SETUP, {"cap": None})
        self.bridge = _Bridge(self._page)

    def test_one_week_rows(self):
        ok, payload = self.bridge.fetch_sodaubai_rows("6a1", 4, show_goi_y=True)
        self.assertTrue(ok, payload)
        self.assertEqual((payload["week"], payload["lop"], payload["class_id"], payload["khoi_hoc"]), (4, "6A1", "123", "6"))
        rows = payload["rows"]
        self.assertEqual([r["tiet"] for r in rows], ["1", "2", "3"])
        self.assertEqual([(r["thu"], r["thu_full"], r["buoi"]) for r in rows][0], ("2", "2\n08/09/2025", "Sáng"))
        self.assertEqual([(r["has_data"], r["is_scheduled"], r["is_unplanned"]) for r in rows],
                         [(True, False, False), (False, True, False), (False, False, True)])
        self.assertEqual([r["add_btn_index"] for r in rows], [-1, 0, 1])
        self.assertEqual((rows[1]["ppct_hint"], rows[1]["red_texts"]), ("13", ["Bài 2"]))
        url, body = self.requests[0]
        self.assertIn("load=app.sodaubai.serv.so_dau_bai", url)
        self.assertIn("my_token=tok", url)
        self.assertEqual({k: body[k] for k in ("lopHoc", "khoiHoc", "tuanHoc", "capHoc", "show_goi_y", "my_user_id")},
                         {"lopHoc": "123", "khoiHoc": "6", "tuanHoc": "4", "capHoc": "", "show_goi_y": "1", "my_user_id": "u1"})

    def test_class_meta_skips_store_lookup(self):
        ok, payload = self.bridge.fetch_sodaubai_rows("x", 2, class_meta={"value": "9", "text": "7B", "cap": "2"})
        self.assertTrue(ok, payload)
        self.assertEqual((payload["class_id"], payload["lop"], payload["khoi_hoc"]), ("9", "7B", "7"))
        self.assertEqual(self.requests[0][1]["capHoc"], "2")

    def test_one_week_errors(self):
        self.assertEqual(self.bridge.fetch_sodaubai_rows("9Z", 1),
                         (False, "Không tìm thấy lớp 9Z trong store hiện tại"))
        self.responses[1] = "http"
        self.assertEqual(self.bridge.fetch_sodaubai_rows("6A1", 1), (False, "Fetch service thất bại: HTTP 500"))
        self.responses[1] = "abort"
        ok, message = self.bridge.fetch_sodaubai_rows("6A1", 1)
        self.assertFalse(ok)
        self.assertTrue(message.startswith("Lỗi fetch service:"), message)

    def test_bulk_weeks(self):
        self.responses.update({3: "http", 5: "wrong_week"})
        ok, payload = self.bridge.fetch_sodaubai_rows_bulk("6A1", [5, 2, "3", 2, 0], concurrency=2)
        self.assertTrue(ok, payload)
        self.assertEqual((payload["requested_count"], payload["concurrency"]), (3, 2))
        results = payload["results"]
        self.assertEqual([r["requested_week"] for r in results], [2, 3, 5])
        self.assertTrue(results[0]["ok"])
        self.assertEqual(len(results[0]["payload"]["rows"]), 3)
        self.assertEqual(results[1], {"requested_week": 3, "ok": False, "error": "Fetch service thất bại: HTTP 500"})
        self.assertEqual(results[2]["error"], "Service trả về tuần 6, không khớp tuần yêu cầu 5")
        # Bản nhiều tuần: không có combobox Cấp thì gửi capHoc = 2 (khác bản một tuần, giữ nguyên như cũ).
        self.assertEqual({body["capHoc"] for _, body in self.requests}, {"2"})
        self.assertEqual(sorted(body["tuanHoc"] for _, body in self.requests), ["2", "3", "5"])

    def test_no_ext(self):
        self._page.evaluate("() => { delete window.Ext; }")
        self.assertEqual(self.bridge.fetch_sodaubai_rows("6A1", 1), (False, "ExtJS not available"))
        self.assertEqual(self.bridge.fetch_sodaubai_rows_bulk("6A1", [1]), (False, "ExtJS not available"))


if __name__ == "__main__":
    unittest.main()

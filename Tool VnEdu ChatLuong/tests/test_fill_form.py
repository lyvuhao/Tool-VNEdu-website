"""fill_form (auto_sdb/cdp/form_fill.py) sau khi tách thành các bước + JS riêng (form_fill_js.py).

- Phần không cần trình duyệt: ghép JS, dựng payload, tên field Môn học.
- Phần có trình duyệt: chạy fill_form trên trang ExtJS giả (fixtures/fake_extjs.js) bằng Chromium của
  Playwright. Tự bỏ qua nếu chưa cài; có thể chỉ định file chạy Chromium qua biến VNEDU_TEST_CHROMIUM.
"""

import os
import re
import sys
import unittest

TOOL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOL_DIR)

from auto_sdb.cdp import form_fill_js as J  # noqa: E402
from auto_sdb.cdp.form_fill import FormFillMixin  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "fake_extjs.js")


class FillFormStaticTests(unittest.TestCase):
    def test_js_assembly(self):
        parts = [J.JS_FILL_PRELUDE, J.JS_FILL_FIND_POPUP, J.JS_FILL_STORE_HELPERS, J.JS_FILL_LOOKUP,
                 J.JS_FILL_COMBO_LOADING, J.JS_FILL_SETTERS, J.JS_FILL_MAIN]
        self.assertEqual(J.JS_FILL_POPUP_FORM, "".join(parts))
        self.assertTrue(J.JS_FILL_POPUP_FORM.startswith("async (args) => {"))
        self.assertTrue(J.JS_FILL_POPUP_FORM.rstrip().endswith("}"))
        for name in ("findPopupFormPanel", "getFieldStore", "findRecordInStore", "ensureComboStoreLoaded",
                     "waitForDependentCombo", "setComboField", "setPlainField"):
            self.assertEqual(len(re.findall(r"function " + name + r"\(", J.JS_FILL_POPUP_FORM)), 1, name)
        self.assertEqual(J.JS_FILL_POPUP_FORM.count("{") , J.JS_FILL_POPUP_FORM.count("}"))

    def test_mon_hoc_candidates(self):
        self.assertEqual(FormFillMixin._mon_hoc_field_candidates(None)[0], "mon_hoc_id")
        found = FormFillMixin._mon_hoc_field_candidates("mon_x")
        self.assertEqual(found[0], "mon_x")
        self.assertEqual(len(found), len(set(found)))
        self.assertEqual(FormFillMixin._mon_hoc_field_candidates("monhoc").count("monhoc"), 1)

    def test_payload(self):
        payload = FormFillMixin._build_fill_form_payload(12, 0, "NX", "", phan_mon_index=3, noi_dung="---",
                                                         mon_hoc_candidates=["mon_hoc_id"])
        self.assertEqual(payload["ppct"], "12")
        self.assertEqual(payload["phanMonVal"], "3")
        self.assertIsNone(payload["monHocVal"])
        self.assertTrue(payload["autoNoiDung"])
        self.assertFalse(FormFillMixin._build_fill_form_payload(1, 0, "", "", noi_dung="Bài 1")["autoNoiDung"])


class FillFormBrowserTests(unittest.TestCase):
    MON = [{"id": 11, "name": "Toán"}, {"id": 12, "name": "Ngữ văn"}]
    PHAN = {"11": [{"id": 101, "name": "Đại số"}, {"id": 102, "name": "Hình học"}]}
    XL = [{"id": "T", "name": "Tốt"}]
    ARGS = dict(ppct="12", hs_nghi="1", nhan_xet="Lớp học tốt", diem="9", phan_mon_index="102", xep_loai="T",
                noi_dung="Bài 5", mon_hoc_index="11", phan_mon_text="Hình học", mon_hoc_text="Toán")

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
        with open(FIXTURE, encoding="utf-8") as fh:
            cls._fake = fh.read()

    @classmethod
    def tearDownClass(cls):
        cls._browser.close()
        cls._pw.stop()

    def run_fill(self, scenario=None, **overrides):
        from auto_sdb.cdp.bridge import ChromeBridge
        page = self._page
        page.goto("about:blank")
        page.add_script_tag(content=self._fake)
        page.evaluate("s => setupFake(s)", dict({"mon": self.MON, "phanByMon": self.PHAN, "xepLoai": self.XL}, **(scenario or {})))
        bridge = ChromeBridge()
        bridge.page = page
        bridge._connected = True
        result = bridge.fill_form(**dict(self.ARGS, **overrides))
        return result, page.evaluate("() => fakeState()")

    def test_fill_all_fields(self):
        (ok, msg), state = self.run_fill()
        self.assertTrue(ok, msg)
        self.assertEqual(state["values"], {"mon_hoc_id": "11", "phan_mon_id": "102", "tiet_ppct": "12", "soluong_nghi": "1",
                                           "noi_dung": "Bài 5", "nhan_xet": "Lớp học tốt", "diem": "9", "xep_loai": "T"})
        self.assertIn("phan_mon_store: ready=2", msg)

    def test_lazy_store_and_bind_store(self):
        (ok, msg), state = self.run_fill({"monLazy": True, "bindXepLoai": True})
        self.assertTrue(ok, msg)
        self.assertIn("mon_hoc_id.expand", state["events"])
        self.assertIn("xep_loai.bindStore", state["events"])

    def test_errors(self):
        (ok, msg), _ = self.run_fill(phan_mon_index="999", phan_mon_text="Không có")
        self.assertFalse(ok)
        self.assertIn("phan_mon: option not found value=999", msg)
        (ok, msg), _ = self.run_fill({"missingField": "tiet_ppct"})
        self.assertEqual((ok, msg), (False, "Lỗi nhập form: Field not found: tiet_ppct"))
        (ok, msg), _ = self.run_fill({"noPopup": True})
        self.assertEqual((ok, msg), (False, "Lỗi nhập form: Khong tim thay ExtJS form popup"))

    def test_not_connected(self):
        from auto_sdb.cdp.bridge import ChromeBridge
        self.assertEqual(ChromeBridge().fill_form("1", "0", "", ""), (False, "Chưa kết nối CDP"))


if __name__ == "__main__":
    unittest.main()

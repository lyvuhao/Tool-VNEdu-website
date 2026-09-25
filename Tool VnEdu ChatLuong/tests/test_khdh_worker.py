"""Worker nhập Sổ đầu bài theo KHDH (auto_sdb/app/schedule_worker_khdh.py + khdh_rows.py).

Dùng ChromeBridge giả theo kịch bản — không cần Chrome/tkinter.
"""

import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auto_sdb.app.schedule_worker_khdh as W  # noqa: E402
from auto_sdb.app import khdh_rows as R  # noqa: E402

CLOSED = "Target page, context or browser has been closed"


def row(i, **extra):
    # rowIdx bắt đầu từ 1 như bảng thật (hàng 0 là tiêu đề); add_btn_index bắt đầu từ 0.
    base = {"has_add_btn": True, "rowIdx": i + 1, "add_btn_index": i, "thu": "2", "buoi": "Sáng", "tiet": str(i + 1),
            "mon_hoc_id": "11", "phan_mon_id": "", "ppct_hint": f"Tiết {10 + i}", "noi_dung_hint": f"Bài {i}",
            "ngay": "8/9/2025"}
    base.update(extra)
    return base


def snapshot_for(r, **override):
    values = {"thu": r["thu"], "tiet": r["tiet"], "ngay": "08/09/2025", "mon_hoc_id": r["mon_hoc_id"],
              "phan_mon_id": "", "tiet_ppct": R.digits(r["ppct_hint"]), "noi_dung": r["noi_dung_hint"]}
    values.update(override)
    return {"fields": {k: {"value": v, "raw": v} for k, v in values.items()}}


class FakeBridge:
    """ChromeBridge giả: `script` quy định kết quả từng thao tác."""

    script = {}

    def __init__(self, port=None):
        self.calls = []
        self.rows = self.script.get("rows", [])
        self.current = None
        FakeBridge.last = self

    def _get(self, key, default):
        value = self.script.get(key, default)
        return value.pop(0) if isinstance(value, list) and value and key.endswith("_seq") else value

    def connect(self): return True, "ok"
    def setup_dialog_auto_accept(self): pass
    def select_dropdown(self, label, text):
        self.calls.append(("select", label, text))
        return self.script.get("select", (True, "ok"))
    def get_lop_options(self): return True, self.script.get("lops", ["6A1"])
    def set_goi_y_khdh_mode(self, flag): return True, "on"
    def read_khdh_suggested_rows(self): return True, copy.deepcopy(self.rows)
    def click_add_button(self, row_index=-1, row_dom_index=-1):
        self.current = next(r for r in self.rows if r["add_btn_index"] == row_index)
        self.calls.append(("click", row_index))
        return True, "clicked"
    def wait_for_lesson_form(self, **k): return True, "open"
    def get_open_lesson_form_snapshot(self):
        return True, snapshot_for(self.current, **self.script.get("popup_override", {}))
    def fill_form_minimal(self, **k):
        self.calls.append(("fill_minimal", k["nhan_xet"]))
        return True, "min ok"
    def fill_form(self, **k):
        self.calls.append(("fill_full", k["ppct"]))
        return True, "full ok"
    def wait_for_form_ready_to_save(self, **k): pass
    def save_form_auto(self, buoi_hoc=""):
        return self._get("save_seq", (True, "saved", {}))
    def wait_for_slot_data_fetch(self, *a, **k): return False, "no data", None
    def wait_for_slot_data(self, *a, **k): return False, "no data", None
    def close_form(self): self.calls.append(("close",))
    def wait_for_lesson_form_closed(self, **k): pass
    def cleanup_after_automation(self, restore_view_mode=True): return True, "clean"
    def disconnect(self): pass


class _Queue:
    def __init__(self): self.items = []
    def put(self, item): self.items.append(item)


class _Stop:
    def __init__(self, after=None): self.after, self.n = after, 0
    def is_set(self):
        self.n += 1
        return self.after is not None and self.n > self.after


class _App:
    def __init__(self, stop_after=None):
        self._schedule_queue = _Queue()
        self._schedule_stop_event = _Stop(stop_after)
        self._schedule_stop_reason = ""

    def _format_schedule_slot_label(self, thu, buoi, tiet):
        return f"Thứ {thu} {buoi} Tiết {tiet}"


def run_job(script, params=None, stop_after=None):
    FakeBridge.script = script
    W.ChromeBridge = FakeBridge
    app = _App(stop_after)
    base = {"tuan_from": 1, "tuan_to": 1, "lop_list": ["6A1"], "port": 9224, "hs_nghi": "0", "diem": "",
            "nhan_xet_raw": "Tốt"}
    base.update(params or {})
    W.KhdhScheduleJob(app, base).run()
    events = app._schedule_queue.items
    done = events[-1][1]
    statuses = [e[1]["status"] for e in events if e[0] == "slot_result"]
    return app, events, done, statuses


class KhdhRowsTests(unittest.TestCase):
    def test_as_index_keeps_zero(self):
        self.assertEqual([R.as_index(v) for v in (0, "0", 3, None, "", "x", " 2 ")], [0, 0, 3, -1, -1, -1, 2])

    def test_helpers(self):
        self.assertEqual(R.digits("Tiết 12a"), "12")
        self.assertEqual(R.normalize_text("  Bài 1:\nSố  học "), "bai 1: so hoc")
        self.assertEqual(R.normalize_date_token("Ngày 8/9/2025"), "08/09/2025")
        self.assertEqual(R.parse_nhan_xet_items(" | "), ["Lớp học chăm ngoan"])
        self.assertEqual(R.build_work_items(["A", "B"], 0, 2, 1, 2), [(0, "A", 2), (1, "B", 1), (1, "B", 2)])

    def test_verify_popup(self):
        r = row(0)
        ok, hard, soft, popup = R.verify_snapshot_matches_row(snapshot_for(r), r)
        self.assertTrue(ok)
        self.assertEqual((hard, soft), ([], []))
        ok, hard, _soft, _ = R.verify_snapshot_matches_row(snapshot_for(r, tiet="5"), r)
        self.assertFalse(ok)
        self.assertEqual(hard, ["tiet mismatch 5 != 1"])
        ok, _hard, soft, _ = R.verify_snapshot_matches_row(snapshot_for(r, tiet_ppct="99"), r)
        self.assertTrue(ok)
        self.assertEqual(soft, ["PPCT mismatch 99 != 10"])

    def test_fill_targets(self):
        targets = R.resolve_fill_targets({"ppct_raw": "", "ppct": "", "noi_dung": ""}, row(0))
        self.assertEqual((targets["ppct"], targets["noi_dung"]), ("Tiết 10", "Bài 0"))
        self.assertEqual(R.missing_fill_fields({"ppct": "", "noi_dung": ""}), ["PPCT", "nội dung"])
        self.assertTrue(R.popup_has_khdh_payload({"ppct": "10", "noi_dung": "Bài"}))


class KhdhJobTests(unittest.TestCase):
    def test_happy_path(self):
        _app, events, done, statuses = run_job({"rows": [row(0), row(1)]})
        self.assertEqual(statuses, ["success", "success"])
        self.assertEqual((done["completed"], done["errors"], done["stopped"]), (2, 0, False))
        self.assertEqual(done["last_success_ppct"], 11)
        self.assertEqual([c[0] for c in FakeBridge.last.calls].count("fill_minimal"), 2)

    def test_first_add_button_index_zero(self):
        run_job({"rows": [row(0)]})
        self.assertIn(("click", 0), FakeBridge.last.calls)

    def test_popup_without_khdh_data_uses_full_fill(self):
        run_job({"rows": [row(0)], "popup_override": {"tiet_ppct": "", "noi_dung": ""}})
        self.assertIn(("fill_full", "Tiết 10"), FakeBridge.last.calls)

    def test_popup_wrong_row_stops_with_resume(self):
        app, _events, done, statuses = run_job({"rows": [row(0), row(1)], "popup_override": {"tiet": "9"}})
        self.assertEqual(statuses, ["error_verify_popup"])
        self.assertTrue(done["stopped"])
        self.assertEqual(done["resume_state"]["next_slot_idx"], 0)
        self.assertEqual(done["resume_state"]["next_row_key"], R.row_resume_key(row(0)))
        self.assertEqual(app._schedule_stop_reason, "popup mở sai row KHDH")

    def test_ambiguous_save_stops(self):
        app, _e, done, statuses = run_job({"rows": [row(0), row(1)],
                                           "save_seq": [(False, "timeout", {"request_sent": True})]})
        self.assertEqual(statuses, ["error_save_ambiguous"])
        self.assertTrue(done["stopped"])
        self.assertEqual(app._schedule_stop_reason, "save KHBD/KHDH mơ hồ cần xác minh")

    def test_chrome_closed(self):
        _app, events, done, _s = run_job({"rows": [row(0)], "select": (False, CLOSED)})
        self.assertTrue(any(e[0] == "error" and "CDP/Chrome đã đóng" in e[1] for e in events))
        self.assertEqual(done["errors"], 1)

    def test_resume_from_row_key(self):
        rows = [row(0), row(1), row(2)]
        params = {"resume_state": {"next_lop_idx": 0, "next_tuan_num": 1, "next_slot_idx": 0,
                                   "next_row_key": R.row_resume_key(rows[2]), "completed": 2}}
        _app, _e, done, statuses = run_job({"rows": rows}, params)
        self.assertEqual([c for c in FakeBridge.last.calls if c[0] == "click"], [("click", 2)])
        self.assertEqual((statuses, done["completed"]), (["success"], 3))

    def test_user_stop(self):
        _app, _e, done, statuses = run_job({"rows": [row(0), row(1)]}, stop_after=2)
        self.assertEqual(statuses, ["success"])
        self.assertTrue(done["stopped"])
        self.assertEqual(done["resume_state"]["next_slot_idx"], 1)

    def test_class_not_in_week(self):
        _app, _e, done, statuses = run_job({"rows": [row(0)], "lops": ["7A1"]})
        self.assertEqual(statuses, ["skipped_unavailable_class"])
        self.assertEqual(done["skipped"], 1)


if __name__ == "__main__":
    unittest.main()
